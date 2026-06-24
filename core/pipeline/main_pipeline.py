"""Main Pipeline: V3 AI Intelligence System orchestrator.

Replaces the procedural update_news.py with a class-based pipeline
that integrates Signal Score 2.0, Trend Engine, and Multi-Agent reporting.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.models import SH_TZ, UTC, WAYTOAGI_DEFAULT, RawItem
from core.logging_config import setup_logging
from core.utils import (
    add_hotness_scores,
    atomic_write_text,
    create_session,
    event_time,
    iso,
    make_item_id,
    maybe_fix_mojibake,
    normalize_url,
    parse_iso,
    utc_now,
)
from core.normalize.normalizer import (
    classify_item,
    classify_tags,
    is_ai_related_record,
    normalize_source_for_display,
    sanitize_public_payload,
)
from core.dedup.deduplicator import (
    dedupe_items_by_title_url,
    is_hubtoday_placeholder_title,
    normalize_aihubtoday_records,
)
from core.normalize.translator import add_bilingual_fields, load_title_zh_cache, safeguard_title_zh_cache
from core.archive import load_archive
from core.output import build_latest_payloads
from core.recommend import enrich_recommendation_fields
from core.fetch import collect_all
from core.fetch.opml import fetch_opml_rss
from core.fetch.waytoagi import fetch_waytoagi_recent_7d
from core.agents.analyst_agent import process_items_with_ai
from core.notifier import maybe_send_news_notification

logger = logging.getLogger(__name__)


class Pipeline:
    """V3 AI Intelligence Pipeline — orchestrates all stages from fetch to report."""

    def __init__(self, config: dict | None = None):
        self.config = config or {}

        # Feature gates
        self.signal_score_enabled = self.config.get("signal_score_enabled", True)
        self.trend_engine_enabled = self.config.get("trend_engine_enabled", False)
        self.ai_tldr_enabled = self.config.get("ai_tldr_enabled", True)

        # Lazy-loaded engines
        self._signal_engine: Any = None
        self._trend_detector: Any = None
        self._editor_agent: Any = None
        self._critic_agent: Any = None

    # ── Lazy init helpers ────────────────────────────────────────────────

    def _get_signal_engine(self):
        if self._signal_engine is None:
            from core.signal_score.scorer import SignalScoreEngine
            self._signal_engine = SignalScoreEngine(self.config.get("signal_score"))
        return self._signal_engine

    def _get_trend_detector(self):
        if self._trend_detector is None:
            from core.trend_engine.trend_detector import TrendDetector
            self._trend_detector = TrendDetector(self.config.get("trend_engine"))
        return self._trend_detector

    def _get_editor_agent(self):
        if self._editor_agent is None:
            from core.agents.editor_agent import EditorAgent
            self._editor_agent = EditorAgent(self.config.get("editor"))
        return self._editor_agent

    def _get_critic_agent(self):
        if self._critic_agent is None:
            from core.agents.critic_agent import CriticAgent
            self._critic_agent = CriticAgent(self.config.get("critic"))
        return self._critic_agent

    # ── Main pipeline ─────────────────────────────────────────────────────

    def run(
        self,
        *,
        output_dir: str = "data",
        window_hours: int = 24,
        archive_days: int = 3,
        translate_max_new: int = 80,
        rss_opml: str = "",
        rss_max_feeds: int = 0,
    ) -> dict[str, Any]:
        """Execute the full V3 pipeline and return a result summary."""
        setup_logging()
        now = utc_now()
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        # ── Stage 1: Fetch ────────────────────────────────────────────────
        logger.info("[Pipeline] Stage 1/7: Fetching from all sources...")
        session = create_session()
        raw_items, statuses = collect_all(session, now)
        # OPML RSS (optional)
        rss_feed_statuses: list[dict] = []
        if rss_opml:
            opml_path = Path(rss_opml).expanduser()
            if opml_path.exists():
                rss_items, rss_summary, rss_feed_statuses = fetch_opml_rss(
                    now, opml_path, max_feeds=max(0, int(rss_max_feeds)), session=session,
                )
                raw_items.extend(rss_items)
                statuses.append(rss_summary)

        logger.info("[Pipeline] Fetched %d raw items from %d sources.", len(raw_items), len(statuses))

        # ── Stage 2: Normalize ─────────────────────────────────────────────
        logger.info("[Pipeline] Stage 2/7: Normalizing...")
        archive = load_archive(output_path / "archive.json")
        seen_this_run: set[str] = set()

        for raw in raw_items:
            title = raw.title.strip()
            url = normalize_url(raw.url)
            if not title or not url or not url.startswith("http"):
                continue
            item_id = make_item_id(raw.site_id, raw.source, title, url)
            seen_this_run.add(item_id)
            raw_meta = raw.meta if isinstance(raw.meta, dict) else {}

            existing = archive.get(item_id)
            if existing is None:
                archive[item_id] = {
                    "id": item_id, "site_id": raw.site_id, "site_name": raw.site_name,
                    "source": raw.source, "title": title, "url": url,
                    "published_at": iso(raw.published_at),
                    "first_seen_at": iso(now), "last_seen_at": iso(now),
                    "description": raw.description or "", "meta": raw_meta,
                }
            else:
                existing["site_id"] = raw.site_id
                existing["site_name"] = raw.site_name
                existing["source"] = raw.source
                existing["title"] = title
                existing["url"] = url
                if raw.published_at:
                    if raw.site_id == "opmlrss" or not existing.get("published_at"):
                        existing["published_at"] = iso(raw.published_at)
                existing["last_seen_at"] = iso(now)
                if raw.description:
                    existing["description"] = raw.description or ""

        # Prune archive
        keep_after = now - timedelta(days=archive_days)
        archive = {
            k: v for k, v in archive.items()
            if (parse_iso(v.get("last_seen_at")) or parse_iso(v.get("published_at"))
                or parse_iso(v.get("first_seen_at")) or now) >= keep_after
        }

        # 24h window
        window_start = now - timedelta(hours=window_hours)
        latest_all: list[dict] = []
        for rec in archive.values():
            ts = event_time(rec)
            if ts and ts >= window_start:
                normed = dict(rec)
                normed["title"] = maybe_fix_mojibake(str(normed.get("title", "")))
                normed["source"] = maybe_fix_mojibake(normalize_source_for_display(
                    str(normed.get("site_id", "")), str(normed.get("source", "")), str(normed.get("url", ""))))
                if str(normed.get("site_id", "")) == "aihubtoday" and is_hubtoday_placeholder_title(
                    str(normed.get("title", ""))):
                    continue
                latest_all.append(normed)

        latest_all = normalize_aihubtoday_records(latest_all)
        latest_all.sort(key=lambda x: event_time(x) or datetime.min.replace(tzinfo=UTC), reverse=True)

        # Classify + tag
        for rec in latest_all:
            rec["category"] = classify_item(rec)
            rec["tags"] = classify_tags(rec)

        # AI filter
        latest_ai = [r for r in latest_all if is_ai_related_record(r)]

        # ── Stage 3: Translate ─────────────────────────────────────────────
        logger.info("[Pipeline] Stage 3/7: Translating titles...")
        title_cache_path = output_path / "title-zh-cache.json"
        title_cache = load_title_zh_cache(title_cache_path)
        latest_ai, latest_all, title_cache = add_bilingual_fields(
            latest_ai, latest_all, session, title_cache, max_new_translations=max(0, int(translate_max_new)))

        # ── Stage 4: Dedup ─────────────────────────────────────────────────
        logger.info("[Pipeline] Stage 4/7: Deduplicating...")
        items_ai = dedupe_items_by_title_url(latest_ai)
        items_all = dedupe_items_by_title_url(latest_all)

        # Hotness
        add_hotness_scores(items_ai)
        add_hotness_scores(items_all)

        if len(items_ai) < 3:
            logger.error("[Pipeline] Too few AI items (%d), aborting.", len(items_ai))
            return {"success": False, "error": "Too few AI items", "item_count": len(items_ai)}

        # ── Stage 5: Signal Score 2.0 ──────────────────────────────────────
        if self.signal_score_enabled:
            logger.info("[Pipeline] Stage 5/7: Signal Score 2.0...")
            signal_engine = self._get_signal_engine()
            signal_engine.score_batch(items_ai)
            signal_engine.score_batch(items_all)
        else:
            logger.info("[Pipeline] Stage 5/7: Skipped (signal_score_enabled=False)")

        # ── Stage 6: AI TL;DR ──────────────────────────────────────────────
        if self.ai_tldr_enabled:
            logger.info("[Pipeline] Stage 6/7: AI TL;DR generation...")
            try:
                items_ai = process_items_with_ai(items_ai)
            except Exception as exc:
                logger.warning("[Pipeline] AI TL;DR failed (non-fatal): %s", exc)
        else:
            logger.info("[Pipeline] Stage 6/7: Skipped (ai_tldr_enabled=False)")

        # Recommendation fields (backward compat)
        enrich_recommendation_fields(items_ai)
        enrich_recommendation_fields(items_all)

        # ── Stage 7: Trend Engine + Report ─────────────────────────────────
        trend_result: dict = {}
        report: str = ""
        if self.trend_engine_enabled:
            logger.info("[Pipeline] Stage 7/7: Trend Engine + Report generation...")
            try:
                trend_detector = self._get_trend_detector()
                trend_result = trend_detector.analyze(items_ai)
                editor = self._get_editor_agent()
                report = editor.generate_report(items_ai, trend_result)
                critic = self._get_critic_agent()
                validation = critic.validate(report)
                if not validation.get("valid"):
                    report = critic.improve(report, validation)
                logger.info("[Pipeline] Report generated (%d chars, validated: %s).",
                            len(report), validation.get("level", "unknown"))
            except Exception as exc:
                logger.warning("[Pipeline] Trend/Report failed (non-fatal): %s", exc)
        else:
            logger.info("[Pipeline] Stage 7/7: Skipped (trend_engine_enabled=False)")

        # ── Output ─────────────────────────────────────────────────────────
        ai_tldr_count = sum(1 for item in items_ai if item.get("tldr"))

        # Site stats
        site_stat: dict = {}
        raw_count_by_site: dict = {}
        for rec in latest_all:
            sid = rec["site_id"]
            raw_count_by_site[sid] = raw_count_by_site.get(sid, 0) + 1

        site_name_by_id: dict = {}
        for rec in latest_all:
            site_name_by_id[rec["site_id"]] = rec["site_name"]
        for s in statuses:
            sid = s["site_id"]
            if sid not in site_name_by_id:
                site_name_by_id[sid] = s.get("site_name") or sid

        for rec in items_ai:
            sid = rec["site_id"]
            if sid not in site_stat:
                site_stat[sid] = {"site_id": sid, "site_name": rec["site_name"], "count": 0,
                                  "raw_count": raw_count_by_site.get(sid, 0)}
            site_stat[sid]["count"] += 1
        for sid, sn in site_name_by_id.items():
            if sid not in site_stat:
                site_stat[sid] = {"site_id": sid, "site_name": sn, "count": 0,
                                  "raw_count": raw_count_by_site.get(sid, 0)}

        latest_payload = {
            "generated_at": iso(now), "window_hours": window_hours,
            "total_items": len(items_ai), "total_items_ai_raw": len(latest_ai),
            "total_items_raw": len(latest_all), "total_items_all_mode": len(items_all),
            "topic_filter": "ai_tech_robotics", "archive_total": len(archive),
            "site_count": len(site_stat),
            "source_count": len({f"{i['site_id']}::{i['source']}" for i in items_ai}),
            "site_stats": sorted(site_stat.values(), key=lambda x: x["count"], reverse=True),
            "items": items_ai, "items_ai": items_ai, "items_all": items_all,
        }

        archive_payload = {
            "generated_at": iso(now), "total_items": len(archive),
            "items": sorted(archive.values(),
                            key=lambda x: parse_iso(x.get("last_seen_at")) or datetime.min.replace(tzinfo=UTC),
                            reverse=True),
        }

        status_payload = {
            "generated_at": iso(now), "sites": statuses,
            "successful_sites": sum(1 for s in statuses if s["ok"]),
            "failed_sites": [s["site_id"] for s in statuses if not s["ok"]],
            "zero_item_sites": [s["site_id"] for s in statuses if s.get("ok") and int(s.get("item_count") or 0) == 0],
            "fetched_raw_items": len(raw_items),
            "items_before_topic_filter": len(latest_all), "items_in_24h": len(items_ai),
            "ai_processing": {"tldr_items": ai_tldr_count},
            "rss_opml": {
                "enabled": bool(rss_opml), "feed_total": len(rss_feed_statuses),
                "ok_feeds": sum(1 for s in rss_feed_statuses if s["ok"]),
                "failed_feeds": [s.get("feed_url") for s in rss_feed_statuses if not s["ok"]],
            },
        }

        # Write outputs
        latest_path = output_path / "latest-24h.json"
        latest_all_path = output_path / "latest-24h-all.json"
        archive_path = output_path / "archive.json"
        status_path = output_path / "source-status.json"
        waytoagi_path = output_path / "waytoagi-7d.json"

        slim_payload, all_payload = build_latest_payloads(latest_payload)
        atomic_write_text(latest_path, json.dumps(sanitize_public_payload(slim_payload),
                                                   ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        atomic_write_text(latest_all_path, json.dumps(sanitize_public_payload(all_payload),
                                                       ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        atomic_write_text(archive_path, json.dumps(sanitize_public_payload(archive_payload),
                                                    ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        atomic_write_text(status_path, json.dumps(sanitize_public_payload(status_payload),
                                                   ensure_ascii=False, indent=2), encoding="utf-8")

        # WaytoAGI
        try:
            waytoagi_payload = fetch_waytoagi_recent_7d(session, now, WAYTOAGI_DEFAULT)
        except Exception as exc:
            logger.warning("[Pipeline] WaytoAGI fetch failed: %s", exc)
            waytoagi_payload = {"generated_at": iso(now), "count_7d": 0, "warning": str(exc), "has_error": True}

        atomic_write_text(waytoagi_path, json.dumps(sanitize_public_payload(waytoagi_payload),
                                                     ensure_ascii=False, indent=2), encoding="utf-8")

        # Title cache
        archive_titles = {rec.get("title") for rec in archive.values() if rec.get("title")}
        title_cache = {k: v for k, v in title_cache.items() if k in archive_titles}
        safeguard_title_zh_cache(title_cache_path, title_cache)
        atomic_write_text(title_cache_path, json.dumps(sanitize_public_payload(title_cache),
                                                        ensure_ascii=False, indent=2), encoding="utf-8")

        # Webhook notification
        try:
            maybe_send_news_notification(items_ai)
        except Exception as exc:
            logger.warning("[Pipeline] Notification failed (non-fatal): %s", exc)

        result = {
            "success": True,
            "items_ai": len(items_ai),
            "items_all": len(items_all),
            "archive": len(archive),
            "successful_sources": sum(1 for s in statuses if s["ok"]),
            "signal_scored": self.signal_score_enabled,
            "trends_detected": bool(trend_result.get("bursts")),
            "report_length": len(report),
        }
        logger.info("[Pipeline] Complete: %s", result)
        return result


def _load_sources_config() -> dict[str, Any]:
    from pathlib import Path
    config_path = Path(__file__).resolve().parent.parent.parent / "config" / "sources.yaml"
    if config_path.exists():
        try:
            import yaml
            with open(config_path, "r", encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {}
        except Exception as e:
            logger.warning("Failed to load config/sources.yaml: %s", e)
    return {}


# ── CLI entry point (backward compatible with scripts/update_news.py) ──────

def main() -> int:
    """CLI entry point — mirrors the old scripts/update_news.py interface."""
    import argparse
    import os
    
    cfg = _load_sources_config()
    pipeline_cfg = cfg.get("pipeline", {})
    
    # 动态将 yaml 中的默认模型写入环境变量以供全局使用
    default_model = cfg.get("openrouter_default_model")
    if default_model and not os.environ.get("OPENROUTER_MODEL"):
        os.environ["OPENROUTER_MODEL"] = str(default_model)

    parser = argparse.ArgumentParser(description="V3 AI News Radar Pipeline")
    parser.add_argument("--output-dir", default="data")
    parser.add_argument("--window-hours", type=int, default=pipeline_cfg.get("window_hours", 24))
    parser.add_argument("--archive-days", type=int, default=pipeline_cfg.get("archive_days", 3))
    parser.add_argument("--translate-max-new", type=int, default=pipeline_cfg.get("translate_max_new", 80))
    parser.add_argument("--rss-opml", default="")
    parser.add_argument("--rss-max-feeds", type=int, default=0)
    parser.add_argument("--trend-engine", action="store_true", default=False, help="Enable Trend Engine")
    args = parser.parse_args()

    config = {"trend_engine_enabled": args.trend_engine}
    pipeline = Pipeline(config)
    result = pipeline.run(
        output_dir=args.output_dir,
        window_hours=args.window_hours,
        archive_days=args.archive_days,
        translate_max_new=args.translate_max_new,
        rss_opml=args.rss_opml,
        rss_max_feeds=args.rss_max_feeds,
    )

    if not result.get("success"):
        return 1
    print(f"Pipeline complete: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Aggregate updates from multiple AI news sites and produce 24h snapshot data.

This is the thin CLI entry point.  All logic has been extracted into:
  scripts/models.py          – constants and data model
  scripts/utils.py           – pure utility functions
  scripts/topic_filter.py    – AI topic filtering
  scripts/dedup.py           – deduplication
  scripts/translate.py       – EN→ZH title translation
  scripts/archive.py         – archive loading
  scripts/output.py          – payload splitting
  scripts/fetchers/          – source-specific fetchers
  scripts/logging_config.py  – logging setup
"""

from __future__ import annotations

import sys
from pathlib import Path as _Path

# Ensure project root is on sys.path when run as `python scripts/update_news.py`
_project_root = str(_Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import argparse
import json
import logging
from datetime import datetime as dt_cls, timedelta, timezone
from pathlib import Path

from scripts.models import SH_TZ, UTC, WAYTOAGI_DEFAULT
from scripts.logging_config import setup_logging
from scripts.utils import (
    add_hotness_scores,
    create_session,
    event_time,
    iso,
    make_item_id,
    maybe_fix_mojibake,
    normalize_url,
    parse_iso,
    utc_now,
)
from scripts.topic_filter import (
    classify_item,
    classify_tags,
    is_ai_related_record,
    normalize_source_for_display,
    sanitize_public_payload,
)
from scripts.dedup import (
    dedupe_items_by_title_url,
    is_hubtoday_placeholder_title,
    normalize_aihubtoday_records,
)
from scripts.translate import add_bilingual_fields, load_title_zh_cache
from scripts.archive import load_archive
from scripts.output import build_latest_payloads
from scripts.fetchers import collect_all
from scripts.fetchers.opml import fetch_opml_rss
from scripts.fetchers.waytoagi import fetch_waytoagi_recent_7d
from scripts.ai_processor import process_items_with_ai
from scripts.notifier import maybe_send_news_notification

logger = logging.getLogger(__name__)


def main() -> int:
    setup_logging()

    parser = argparse.ArgumentParser(description="Aggregate AI news updates from multiple sources")
    parser.add_argument("--output-dir", default="data", help="Directory for output JSON files")
    parser.add_argument("--window-hours", type=int, default=24, help="24h window size")
    parser.add_argument("--archive-days", type=int, default=21, help="Keep archive for N days")
    parser.add_argument("--translate-max-new", type=int, default=80, help="Max new EN->ZH title translations per run")
    parser.add_argument("--rss-opml", default="", help="Optional OPML file path to include RSS sources")
    parser.add_argument("--rss-max-feeds", type=int, default=0, help="Optional max OPML RSS feeds to fetch (0 means all)")
    args = parser.parse_args()

    now = utc_now()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    archive_path = output_dir / "archive.json"
    latest_path = output_dir / "latest-24h.json"
    latest_all_path = output_dir / "latest-24h-all.json"
    status_path = output_dir / "source-status.json"
    waytoagi_path = output_dir / "waytoagi-7d.json"
    title_cache_path = output_dir / "title-zh-cache.json"

    archive = load_archive(archive_path)

    session = create_session()
    raw_items, statuses = collect_all(session, now)
    rss_feed_statuses: list[dict] = []

    if args.rss_opml:
        opml_path = Path(args.rss_opml).expanduser()
        if opml_path.exists():
            rss_items, rss_summary_status, rss_feed_statuses = fetch_opml_rss(
                now,
                opml_path,
                max_feeds=max(0, int(args.rss_max_feeds)),
                session=session,
            )
            raw_items.extend(rss_items)
            statuses.append(rss_summary_status)
        else:
            statuses.append(
                {
                    "site_id": "opmlrss",
                    "site_name": "OPML RSS",
                    "ok": False,
                    "item_count": 0,
                    "duration_ms": 0,
                    "error": f"OPML not found: {opml_path}",
                    "feed_count": 0,
                    "ok_feed_count": 0,
                    "failed_feed_count": 0,
                }
            )

    seen_this_run: set[str] = set()

    for raw in raw_items:
        title = raw.title.strip()
        url = normalize_url(raw.url)
        if not title or not url:
            continue
        if not url.startswith("http"):
            continue

        item_id = make_item_id(raw.site_id, raw.source, title, url)
        seen_this_run.add(item_id)

        existing = archive.get(item_id)
        if existing is None:
            archive[item_id] = {
                "id": item_id,
                "site_id": raw.site_id,
                "site_name": raw.site_name,
                "source": raw.source,
                "title": title,
                "url": url,
                "published_at": iso(raw.published_at),
                "first_seen_at": iso(now),
                "last_seen_at": iso(now),
                "description": raw.description or "",
            }
        else:
            existing["site_id"] = raw.site_id
            existing["site_name"] = raw.site_name
            existing["source"] = raw.source
            existing["title"] = title
            existing["url"] = url
            if raw.published_at:
                # OPML RSS may fix previously wrong publish times; allow overwrite.
                if raw.site_id == "opmlrss" or not existing.get("published_at"):
                    existing["published_at"] = iso(raw.published_at)
            existing["last_seen_at"] = iso(now)
            if raw.description:
                existing["description"] = raw.description

    # Prune old archive
    keep_after = now - timedelta(days=args.archive_days)
    pruned: dict[str, dict] = {}
    for item_id, record in archive.items():
        ts = (
            parse_iso(record.get("last_seen_at"))
            or parse_iso(record.get("published_at"))
            or parse_iso(record.get("first_seen_at"))
            or now
        )
        if ts >= keep_after:
            pruned[item_id] = record
    archive = pruned

    # 24h view
    window_start = now - timedelta(hours=args.window_hours)
    latest_items_all: list[dict] = []
    for record in archive.values():
        ts = event_time(record)
        if not ts:
            continue
        if ts >= window_start:
            normalized = dict(record)
            normalized["title"] = maybe_fix_mojibake(str(normalized.get("title") or ""))
            normalized["source"] = maybe_fix_mojibake(normalize_source_for_display(
                str(normalized.get("site_id") or ""),
                str(normalized.get("source") or ""),
                str(normalized.get("url") or ""),
            ))
            if str(normalized.get("site_id") or "") == "aihubtoday" and is_hubtoday_placeholder_title(
                str(normalized.get("title") or "")
            ):
                continue
            latest_items_all.append(normalized)

    latest_items_all = normalize_aihubtoday_records(latest_items_all)

    latest_items_all.sort(key=lambda x: event_time(x) or dt_cls.min.replace(tzinfo=UTC), reverse=True)

    # 为每条新闻打上主题分类标签（AI / 科技 / 数码 / 电脑硬件）+ 细分标签
    for record in latest_items_all:
        record["category"] = classify_item(record)
        record["tags"] = classify_tags(record)

    latest_items = [record for record in latest_items_all if is_ai_related_record(record)]
    title_cache = load_title_zh_cache(title_cache_path)
    latest_items, latest_items_all, title_cache = add_bilingual_fields(
        latest_items,
        latest_items_all,
        session,
        title_cache,
        max_new_translations=max(0, args.translate_max_new),
    )
    latest_items_ai_dedup = dedupe_items_by_title_url(latest_items)
    latest_items_all_dedup = dedupe_items_by_title_url(latest_items_all)

    # Add hotness scores for trending sort
    add_hotness_scores(latest_items_ai_dedup)
    add_hotness_scores(latest_items_all_dedup)

    # Data quality check
    if len(latest_items_ai_dedup) < 3:
        logger.error("Too few AI items (%d), possible source failure", len(latest_items_ai_dedup))
        return 1

    try:
        latest_items_ai_dedup = process_items_with_ai(latest_items_ai_dedup)
    except Exception as exc:
        logger.warning("Optional AI TL;DR processing failed; continuing without it: %s", exc)

    ai_tldr_count = sum(1 for item in latest_items_ai_dedup if item.get("tldr"))

    # site stats
    site_stat: dict[str, dict] = {}
    raw_count_by_site: dict[str, int] = {}
    for record in latest_items_all:
        sid = record["site_id"]
        raw_count_by_site[sid] = raw_count_by_site.get(sid, 0) + 1

    site_name_by_id: dict[str, str] = {}
    for record in latest_items_all:
        site_name_by_id[record["site_id"]] = record["site_name"]
    for s in statuses:
        sid = s["site_id"]
        if sid not in site_name_by_id:
            site_name_by_id[sid] = s.get("site_name") or sid

    for record in latest_items_ai_dedup:
        sid = record["site_id"]
        if sid not in site_stat:
            site_stat[sid] = {
                "site_id": sid,
                "site_name": record["site_name"],
                "count": 0,
                "raw_count": raw_count_by_site.get(sid, 0),
            }
        site_stat[sid]["count"] += 1

    for sid, site_name in site_name_by_id.items():
        if sid in site_stat:
            continue
        site_stat[sid] = {
            "site_id": sid,
            "site_name": site_name,
            "count": 0,
            "raw_count": raw_count_by_site.get(sid, 0),
        }

    latest_payload = {
        "generated_at": iso(now),
        "window_hours": args.window_hours,
        "total_items": len(latest_items_ai_dedup),
        "total_items_ai_raw": len(latest_items),
        "total_items_raw": len(latest_items_all),
        "total_items_all_mode": len(latest_items_all_dedup),
        "topic_filter": "ai_tech_robotics",
        "archive_total": len(archive),
        "site_count": len(site_stat),
        "source_count": len({f"{i['site_id']}::{i['source']}" for i in latest_items_ai_dedup}),
        "site_stats": sorted(site_stat.values(), key=lambda x: x["count"], reverse=True),
        "items": latest_items_ai_dedup,
        "items_ai": latest_items_ai_dedup,
        "items_all": latest_items_all_dedup,
    }

    archive_payload = {
        "generated_at": iso(now),
        "total_items": len(archive),
        "items": sorted(
            archive.values(),
            key=lambda x: parse_iso(x.get("last_seen_at")) or dt_cls.min.replace(tzinfo=UTC),
            reverse=True,
        ),
    }

    status_payload = {
        "generated_at": iso(now),
        "sites": statuses,
        "successful_sites": sum(1 for s in statuses if s["ok"]),
        "failed_sites": [s["site_id"] for s in statuses if not s["ok"]],
        "zero_item_sites": [s["site_id"] for s in statuses if s.get("ok") and int(s.get("item_count") or 0) == 0],
        "fetched_raw_items": len(raw_items),
        "items_before_topic_filter": len(latest_items_all),
        "items_in_24h": len(latest_items_ai_dedup),
        "ai_processing": {
            "tldr_items": ai_tldr_count,
        },
        "rss_opml": {
            "enabled": bool(args.rss_opml),
            "path": "configured" if args.rss_opml else None,
            "feed_total": len(rss_feed_statuses),
            "effective_feed_total": sum(1 for s in rss_feed_statuses if not s.get("skipped")),
            "ok_feeds": sum(1 for s in rss_feed_statuses if s["ok"] and not s.get("skipped")),
            "failed_feeds": [s.get("effective_feed_url") or s["feed_url"] for s in rss_feed_statuses if not s["ok"]],
            "zero_item_feeds": [
                s.get("effective_feed_url") or s["feed_url"]
                for s in rss_feed_statuses
                if s["ok"] and not s.get("skipped") and int(s.get("item_count") or 0) == 0
            ],
            "skipped_feeds": [
                {"feed_url": s["feed_url"], "reason": s.get("skip_reason")}
                for s in rss_feed_statuses
                if s.get("skipped")
            ],
            "replaced_feeds": [
                {"from": s["feed_url"], "to": s.get("effective_feed_url")}
                for s in rss_feed_statuses
                if s.get("replaced") and s.get("effective_feed_url")
            ],
            "feeds": rss_feed_statuses,
        },
    }

    try:
        waytoagi_payload = fetch_waytoagi_recent_7d(session, now, WAYTOAGI_DEFAULT)
    except Exception as exc:
        logger.warning("WaytoAGI fetch failed: %s", exc)
        waytoagi_payload = {
            "generated_at": iso(now),
            "timezone": "Asia/Shanghai",
            "root_url": WAYTOAGI_DEFAULT,
            "history_url": None,
            "window_days": 7,
            "count_7d": 0,
            "updates_7d": [],
            "warning": "WaytoAGI 近7日更新抓取失败",
            "has_error": True,
            "error": str(exc),
        }

    latest_payload, latest_all_payload = build_latest_payloads(latest_payload)

    latest_path.write_text(json.dumps(sanitize_public_payload(latest_payload), ensure_ascii=False, indent=2), encoding="utf-8")
    latest_all_path.write_text(json.dumps(sanitize_public_payload(latest_all_payload), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    archive_path.write_text(
        json.dumps(sanitize_public_payload(archive_payload), ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    status_path.write_text(json.dumps(sanitize_public_payload(status_payload), ensure_ascii=False, indent=2), encoding="utf-8")
    waytoagi_path.write_text(json.dumps(sanitize_public_payload(waytoagi_payload), ensure_ascii=False, indent=2), encoding="utf-8")
    title_cache_path.write_text(json.dumps(sanitize_public_payload(title_cache), ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info(
        "Pipeline complete: %d AI items, %d all-mode items, %d archive, %d successful sources",
        len(latest_items_ai_dedup),
        len(latest_items_all_dedup),
        len(archive),
        sum(1 for s in statuses if s["ok"]),
    )
    print(f"Wrote: {latest_path} ({len(latest_items)} items)")
    print(f"Wrote: {latest_all_path} ({len(latest_items_all_dedup)} all-mode items)")
    print(f"Wrote: {archive_path} ({len(archive)} items)")
    print(f"Wrote: {status_path}")
    print(f"Wrote: {waytoagi_path} ({waytoagi_payload.get('count_7d', 0)} items)")
    print(f"Wrote: {title_cache_path} ({len(title_cache)} entries)")

    try:
        maybe_send_news_notification(latest_items_ai_dedup)
    except Exception as exc:
        logger.warning("Optional webhook notification failed; data output is still valid: %s", exc)

    return 0


# ---------------------------------------------------------------------------
# Re-exports for test compatibility (tests import from scripts.update_news)
# ---------------------------------------------------------------------------
from scripts.models import RawItem, OFFICIAL_AI_MAX_AGE_DAYS, OFFICIAL_AI_FEEDS, BROWSER_UA, UTC  # noqa: F401, E402
from scripts.utils import (  # noqa: F401, E402
    normalize_url,
    host_of_url,
    first_non_empty,
    maybe_fix_mojibake,
    has_cjk,
    is_mostly_english,
    parse_feed_entries_via_xml,
    make_item_id,
    parse_unix_timestamp,
    parse_relative_time_zh,
    parse_date_any,
    decode_escaped_json,
    strip_html_tags,
    truncate_description,
    create_session,
    utc_now,
    iso,
    parse_iso,
)
from scripts.topic_filter import (  # noqa: F401, E402
    AI_KEYWORDS,
    TECH_KEYWORDS,
    NOISE_KEYWORDS,
    COMMERCE_NOISE_KEYWORDS,
    EN_SIGNAL_RE,
    MEANINGFUL_EN_SIGNAL_RE,
    EMAIL_RE,
    SECRET_LIKE_RE,
    BROAD_AI_TERMS,
    TOPHUB_ALLOW_KEYWORDS,
    TOPHUB_BLOCK_KEYWORDS,
    contains_any_keyword,
    contains_meaningful_ai_signal,
    redact_public_text,
    sanitize_public_value,
    sanitize_public_payload,
    has_mojibake_noise,
    normalize_source_for_display,
    is_ai_related_record,
    classify_tags,
)
from scripts.dedup import (  # noqa: F401, E402
    dedupe_items_by_title_url,
    normalize_aihubtoday_records,
    is_hubtoday_placeholder_title,
    is_hubtoday_generic_anchor_title,
)
from scripts.translate import load_title_zh_cache, translate_to_zh_cn, add_bilingual_fields  # noqa: F401, E402
from scripts.archive import load_archive, event_time  # noqa: F401, E402
from scripts.output import build_latest_payloads  # noqa: F401, E402
from scripts.fetchers.official import (  # noqa: F401, E402
    parse_anthropic_news_items,
    parse_openai_codex_changelog_items,
    fetch_feed_as_official_items,
    fetch_official_ai_updates,
)
from scripts.fetchers.newsletters import parse_ai_breakfast_items  # noqa: F401, E402
from scripts.fetchers.builders import parse_follow_builders_items  # noqa: F401, E402
from scripts.fetchers.waytoagi import (  # noqa: F401, E402
    extract_waytoagi_history_url,
    extract_feishu_client_vars,
    block_text,
    clean_update_title,
    parse_ym_heading,
    parse_md_heading,
    infer_shanghai_year_for_month_day,
    extract_waytoagi_recent_updates_from_block_map,
    fetch_waytoagi_recent_7d,
)
from scripts.fetchers.opml import parse_opml_subscriptions  # noqa: F401, E402


if __name__ == "__main__":
    raise SystemExit(main())

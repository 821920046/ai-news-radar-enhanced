"""Optional webhook notifications for AI News Radar summaries."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import requests
from scripts.utils import _env_int

logger = logging.getLogger(__name__)

DEFAULT_HOTNESS_THRESHOLD = 150
DEFAULT_DIGEST_LIMIT = 5
DEFAULT_BREAKING_LIMIT = 10





def _compact(text: Any, limit: int = 180) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)].rstrip() + "..."


def filter_breaking_news(items: list[dict[str, Any]], hotness_threshold: int = DEFAULT_HOTNESS_THRESHOLD) -> list[dict[str, Any]]:
    """Pick high-hotness or explicitly important items for alert-style pushes."""
    breaking: list[dict[str, Any]] = []
    seen: set[str] = set()
    important_tags = {"重磅", "首发", "模型发布", "安全对齐"}

    for item in items:
        title = str(item.get("title_zh") or item.get("title") or "").strip()
        if not title:
            continue
        dedupe_key = title.lower()
        if dedupe_key in seen:
            continue

        score = int(item.get("hotness_score") or 0)
        tags = {str(tag).strip() for tag in item.get("tags") or []}
        if score >= hotness_threshold or tags.intersection(important_tags):
            breaking.append(item)
            seen.add(dedupe_key)

    breaking.sort(key=lambda item: int(item.get("hotness_score") or 0), reverse=True)
    return breaking


def select_digest_items(items: list[dict[str, Any]], limit: int = DEFAULT_DIGEST_LIMIT) -> list[dict[str, Any]]:
    """Pick a stable Top N digest, preferring hotness then newest items."""
    ranked = list(items)
    ranked.sort(
        key=lambda item: (
            int(item.get("hotness_score") or 0),
            str(item.get("published_at") or item.get("first_seen_at") or ""),
        ),
        reverse=True,
    )
    return ranked[: max(0, limit)]


def build_markdown_message(items: list[dict[str, Any]], *, title: str = "AI News Radar") -> str:
    lines = [f"**{title}**"]
    for idx, item in enumerate(items, 1):
        headline = _compact(item.get("title_zh") or item.get("title"), 100)
        source = _compact(item.get("site_name") or item.get("source") or "AI News Radar", 40)
        score = int(item.get("hotness_score") or 0)
        tldr = _compact(item.get("tldr") or item.get("description") or "", 140)
        url = item.get("url") or "#"

        lines.append("")
        lines.append(f"{idx}. [{headline}]({url})")
        lines.append(f"Source: {source} | Hotness: {score}")
        if tldr:
            lines.append(f"TL;DR: {tldr}")
    return "\n".join(lines)


def build_webhook_payload(markdown: str, webhook_type: str) -> dict[str, Any]:
    webhook_type = (webhook_type or "markdown").strip().lower()
    if webhook_type in {"feishu", "lark"}:
        return {"msg_type": "text", "content": {"text": markdown}}
    if webhook_type in {"wechat", "wecom", "dingtalk", "dingding", "markdown"}:
        return {"msgtype": "markdown", "markdown": {"content": markdown}}
    return {"text": markdown}


def send_webhook_notification(items: list[dict[str, Any]], *, title: str = "AI News Radar") -> bool:
    """Send a digest or breaking-news list if WEBHOOK_URL is configured."""
    if not items:
        logger.info("[IM Notifier] No items selected for notification.")
        return False

    webhook_url = os.environ.get("WEBHOOK_URL", "").strip()
    if not webhook_url:
        logger.info("[IM Notifier] WEBHOOK_URL is not set; skipping notification.")
        return False

    webhook_type = os.environ.get("WEBHOOK_TYPE", "markdown")
    markdown = build_markdown_message(items, title=title)
    payload = build_webhook_payload(markdown, webhook_type)

    try:
        response = requests.post(webhook_url, json=payload, timeout=10)
    except requests.exceptions.RequestException as exc:
        logger.error("[IM Notifier] Webhook request failed: %s", exc)
        return False

    if 200 <= response.status_code < 300:
        logger.info("[IM Notifier] Delivered %d items via webhook.", len(items))
        return True

    logger.error("[IM Notifier] Delivery failed: %s %s", response.status_code, response.text[:200])
    return False


def maybe_send_news_notification(items: list[dict[str, Any]]) -> bool:
    """Route notification mode from env while keeping the pipeline optional."""
    mode = os.environ.get("WEBHOOK_MODE", "digest").strip().lower()
    if mode in {"0", "false", "no", "off", "none"}:
        logger.info("[IM Notifier] WEBHOOK_MODE disables notifications.")
        return False

    if mode == "breaking":
        threshold = _env_int("WEBHOOK_HOTNESS_THRESHOLD", DEFAULT_HOTNESS_THRESHOLD, prefix="IM Notifier")
        limit = _env_int("WEBHOOK_BREAKING_LIMIT", DEFAULT_BREAKING_LIMIT, prefix="IM Notifier")
        selected = filter_breaking_news(items, threshold)[: max(0, limit)]
        title = "AI News Radar Breaking Alerts"
    else:
        limit = _env_int("WEBHOOK_DIGEST_LIMIT", DEFAULT_DIGEST_LIMIT, prefix="IM Notifier")
        selected = select_digest_items(items, limit)
        title = "AI News Radar Daily Digest"

    return send_webhook_notification(selected, title=title)

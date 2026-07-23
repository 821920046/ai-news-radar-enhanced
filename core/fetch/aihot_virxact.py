"""AIHOT (aihot.virxact.com) public read-only API fetcher.

为什么用 API 而不是爬页面 / 公共 RSSHub：
aihot.virxact.com 已经在上游用官方 X API 把推文抓取、清洗并结构化，
并对外提供 **只读公共 API**。相比公共 RSSHub（常超时/丢 X API），
直接消费它的 API 更稳定，也能拿到干净的 X 内容。

环境变量（均可选）：
- AIHOT_API_BASE   默认 https://aihot.virxact.com/api/public
- AIHOT_MODE       selected | all（默认 all；只要精选用 selected）
- AIHOT_TAKE       每页条数 1-100（默认 100）
- AIHOT_MAX_PAGES  最多翻页数（默认 5，防止无限循环）
- AIHOT_X_ONLY     “1/true” 时只保留来源为 x.com/twitter.com 的条目（默认否，全量入库）
- AIHOT_CATEGORY   可选，限定分类 ai-models|ai-products|industry|paper|tip

返回结构容错：同时兼容 items/data/results 列表键，以及
 nextCursor/next_cursor/cursor 分页键；单条字段名也做了多别名兼容。
若官方字段名与此不同，请先跑 scripts/probe_aihot.py 看真实 JSON 再微调。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import requests

from core.models import BROWSER_UA, RawItem
from core.utils import maybe_fix_mojibake, parse_date_any

logger = logging.getLogger(__name__)

_DEFAULT_BASE = "https://aihot.virxact.com/api/public"
_X_HOSTS = ("x.com", "twitter.com", "mobile.twitter.com", "nitter")


def _first(item: dict[str, Any], *keys: str) -> str:
    for k in keys:
        v = item.get(k)
        if v:
            return str(v).strip()
    return ""


def _is_x_url(url: str, source: str) -> bool:
    host = urlparse(url).netloc.lower()
    if any(h in host for h in _X_HOSTS):
        return True
    s = source.lower()
    return "x ·" in s or s.startswith("x ") or "twitter" in s or s == "x"


def _extract_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("items", "data", "results", "records"):
            v = payload.get(key)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
    return []


def _extract_cursor(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("nextCursor", "next_cursor", "cursor", "next"):
        v = payload.get(key)
        if v:
            return str(v)
    meta = payload.get("meta") or payload.get("pageInfo") or {}
    if isinstance(meta, dict):
        for key in ("nextCursor", "next_cursor", "cursor", "endCursor"):
            v = meta.get(key)
            if v:
                return str(v)
    return ""


def fetch_aihot_virxact(session: requests.Session, now: datetime) -> list[RawItem]:
    site_id = "aihot"
    site_name = "AI HOT"

    base = os.environ.get("AIHOT_API_BASE", _DEFAULT_BASE).rstrip("/")
    mode = os.environ.get("AIHOT_MODE", "all").strip() or "all"
    try:
        take = max(1, min(100, int(os.environ.get("AIHOT_TAKE", "100"))))
    except ValueError:
        take = 100
    try:
        max_pages = max(1, int(os.environ.get("AIHOT_MAX_PAGES", "5")))
    except ValueError:
        max_pages = 5
    x_only = str(os.environ.get("AIHOT_X_ONLY", "")).strip().lower() in {"1", "true", "yes"}
    category = os.environ.get("AIHOT_CATEGORY", "").strip()

    out: list[RawItem] = []
    seen_urls: set[str] = set()
    cursor = ""

    for _ in range(max_pages):
        params: dict[str, Any] = {"mode": mode, "take": take}
        if category:
            params["category"] = category
        if cursor:
            params["cursor"] = cursor

        resp = session.get(
            f"{base}/items",
            params=params,
            timeout=25,
            headers={"User-Agent": BROWSER_UA, "Accept": "application/json, */*"},
        )
        resp.raise_for_status()
        payload = resp.json()

        rows = _extract_list(payload)
        if not rows:
            break

        for item in rows:
            title = maybe_fix_mojibake(
                _first(item, "title_zh", "titleZh", "title_trans", "title", "name")
            )
            url = _first(item, "url", "link", "permalink", "originalUrl", "sourceUrl")
            if not title or not url or url in seen_urls:
                continue

            source_name = maybe_fix_mojibake(
                _first(item, "source", "sourceName", "source_name", "author", "handle")
            ) or site_name

            if x_only and not _is_x_url(url, source_name):
                continue

            published = parse_date_any(
                _first(item, "publishedAt", "published_at", "date", "createdAt", "publish_time"),
                now,
            ) or now
            desc = maybe_fix_mojibake(_first(item, "summary", "description", "excerpt", "tldr"))

            seen_urls.add(url)
            out.append(
                RawItem(
                    site_id=site_id,
                    site_name=site_name,
                    source=f"AI HOT · {source_name}" if source_name != site_name else site_name,
                    title=title,
                    url=url,
                    published_at=published,
                    meta={
                        "category": _first(item, "category", "topic"),
                        "mode": mode,
                        "is_x": _is_x_url(url, source_name),
                    },
                    description=desc,
                )
            )

        cursor = _extract_cursor(payload)
        if not cursor:
            break

    if not out:
        raise ValueError("No AIHOT (virxact) items parsed — 请用 scripts/probe_aihot.py 核对字段名")
    return out

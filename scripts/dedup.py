"""Deduplication and normalization of news items."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from scripts.models import UTC
from scripts.utils import event_time, normalize_url

logger = logging.getLogger(__name__)


def is_hubtoday_placeholder_title(title: str) -> bool:
    t = (title or "").strip()
    if not t:
        return True
    if "详情见官方介绍" in t:
        return True
    return t in {"原文链接", "查看详情", "点击查看", "详情"}


def is_hubtoday_generic_anchor_title(title: str) -> bool:
    t = (title or "").strip()
    if not t:
        return True
    if is_hubtoday_placeholder_title(t):
        return True
    return bool(re.search(r"\(AI资讯\)\s*$", t))


def normalize_aihubtoday_records(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_url: dict[str, list[dict[str, Any]]] = {}
    keep: list[dict[str, Any]] = []

    for item in items:
        if str(item.get("site_id") or "") != "aihubtoday":
            keep.append(item)
            continue
        url = normalize_url(str(item.get("url") or ""))
        if not url:
            continue
        by_url.setdefault(url, []).append(item)

    for group in by_url.values():
        if not group:
            continue
        preferred = [g for g in group if not is_hubtoday_generic_anchor_title(str(g.get("title") or ""))]
        source = preferred if preferred else group
        best = max(
            source,
            key=lambda x: (
                event_time(x) or datetime.min.replace(tzinfo=UTC),
                str(x.get("id") or ""),
            ),
        )
        keep.append(best)

    keep.sort(key=lambda x: event_time(x) or datetime.min.replace(tzinfo=UTC), reverse=True)
    return keep


def dedupe_items_by_title_url(items: list[dict[str, Any]], **kwargs: Any) -> list[dict[str, Any]]:
    """Deduplicate items by (title, url) key, always picking the latest by event_time.

    Parameters
    ----------
    items : list[dict]
        Items to deduplicate.
    **kwargs :
        ``random_pick`` is accepted (for backward compatibility) but ignored.
        Deduplication is always deterministic: the item with the latest
        event_time and highest id wins in each group.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        site_id = str(item.get("site_id") or "").strip().lower()
        title = str(item.get("title_original") or item.get("title") or "").strip().lower()
        url = normalize_url(str(item.get("url") or ""))
        if site_id == "aihubtoday":
            key = f"url::{url}"
        else:
            key = f"{title}||{url}"
        groups.setdefault(key, []).append(item)

    out: list[dict[str, Any]] = []
    for values in groups.values():
        # Always deterministic: pick the item with the latest event_time.
        chosen = max(
            values,
            key=lambda x: (
                event_time(x) or datetime.min.replace(tzinfo=UTC),
                str(x.get("id") or ""),
            ),
        )
        out.append(chosen)

    out.sort(key=lambda x: event_time(x) or datetime.min.replace(tzinfo=UTC), reverse=True)
    return out

"""WaytoAGI Feishu wiki parser: extracts recent 7-day updates."""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import Any

import requests

from scripts.models import SH_TZ, WAYTOAGI_HISTORY_FALLBACK
from scripts.utils import decode_escaped_json, iso

logger = logging.getLogger(__name__)


def extract_waytoagi_history_url(root_html: str) -> str:
    pattern = r'\{\\"id\\":\\"[^\"]+\\",\\"type\\":\\"mention_doc\\",\\"data\\":\{[^\}]+\}\}'
    for raw in re.findall(pattern, root_html):
        obj = decode_escaped_json(raw)
        if not obj:
            continue
        data = obj.get("data", {})
        title = str(data.get("title") or "")
        if "历史更新" in title or "更新日志" in title:
            raw_url = str(data.get("raw_url") or "").strip()
            if raw_url:
                return raw_url
    return WAYTOAGI_HISTORY_FALLBACK


def extract_feishu_client_vars(page_html: str) -> dict[str, Any]:
    marker = "window.DATA = Object.assign({}, window.DATA, { clientVars: Object("
    idx = page_html.find(marker)
    if idx == -1:
        raise ValueError("Cannot locate Feishu clientVars marker")

    start = idx + len(marker)
    depth = 1
    in_str = False
    escaped = False
    end = None

    for i, ch in enumerate(page_html[start:], start):
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue

        if ch == '"':
            in_str = True
            continue

        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                end = i
                break

    if end is None:
        raise ValueError("Cannot parse Feishu clientVars payload")

    payload = page_html[start:end]
    return json.loads(payload)


def block_text(block_data: dict[str, Any]) -> str:
    text_obj = block_data.get("text", {}) if isinstance(block_data, dict) else {}
    initial = text_obj.get("initialAttributedTexts", {}).get("text", {}) if isinstance(text_obj, dict) else {}
    if not isinstance(initial, dict):
        return ""

    def key_int(k: Any) -> int:
        try:
            return int(k)
        except Exception:
            return 0

    return "".join(str(v) for k, v in sorted(initial.items(), key=lambda kv: key_int(kv[0]))).strip()


def clean_update_title(text: str) -> str:
    text = text.replace("《 》", "").replace("《》", "")
    return re.sub(r"\s+", " ", text).strip()


def parse_ym_heading(text: str) -> tuple[int, int] | None:
    m = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月", text)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def parse_md_heading(text: str) -> tuple[int, int] | None:
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def infer_shanghai_year_for_month_day(now_sh: datetime, month: int, day: int) -> int | None:
    year = now_sh.year
    try:
        candidate = date(year, month, day)
    except Exception:
        return None
    if candidate > (now_sh.date() + timedelta(days=2)):
        year -= 1
    return year


def extract_waytoagi_recent_updates_from_block_map(
    block_map: dict[str, Any],
    now_sh: datetime,
    page_url: str,
) -> list[dict[str, Any]]:
    if not isinstance(block_map, dict) or not block_map:
        return []

    ym_by_heading2: dict[str, tuple[int, int]] = {}
    near_log_parent_ids: set[str] = set()

    for bid, block in block_map.items():
        bd = block.get("data", {})
        btype = bd.get("type")
        if btype not in {"heading1", "heading2", "heading3"}:
            continue
        heading_text = block_text(bd)
        if "近7日更新日志" in heading_text or "近 7 日更新日志" in heading_text:
            parent_id = str(bd.get("parent_id") or "").strip()
            if parent_id:
                near_log_parent_ids.add(parent_id)

    heading3_dates: dict[str, date] = {}

    for bid, block in block_map.items():
        bd = block.get("data", {})
        if bd.get("type") != "heading2":
            continue
        ym = parse_ym_heading(block_text(bd))
        if ym:
            ym_by_heading2[bid] = ym

    for bid, block in block_map.items():
        bd = block.get("data", {})
        if bd.get("type") != "heading3":
            continue
        md = parse_md_heading(block_text(bd))
        if not md:
            continue
        month, day = md
        parent = bd.get("parent_id")
        if near_log_parent_ids and parent not in near_log_parent_ids:
            continue
        year = ym_by_heading2.get(parent, (now_sh.year, month))[0]
        inferred = infer_shanghai_year_for_month_day(now_sh, month, day)
        if inferred is not None:
            year = inferred
        try:
            heading3_dates[bid] = date(year, month, day)
        except Exception:
            continue

    parent_map: dict[str, str] = {}
    for bid, block in block_map.items():
        bd = block.get("data", {})
        parent = str(bd.get("parent_id") or "").strip()
        if parent:
            parent_map[bid] = parent

    def nearest_heading_date(block_id: str) -> date | None:
        cur = parent_map.get(block_id)
        hops = 0
        while cur and hops < 20:
            if cur in heading3_dates:
                return heading3_dates[cur]
            cur = parent_map.get(cur)
            hops += 1
        return None

    updates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for bid, block in block_map.items():
        bd = block.get("data", {})
        if bd.get("type") not in {"bullet", "text", "todo", "ordered"}:
            continue

        day = nearest_heading_date(bid)
        if not day:
            continue
        title = clean_update_title(block_text(bd))
        if not title:
            continue
        key = (day.isoformat(), title)
        if key in seen:
            continue
        seen.add(key)
        updates.append({"date": day.isoformat(), "title": title, "url": page_url})

    return updates


def fetch_waytoagi_recent_7d(session: requests.Session, now_utc: datetime, root_url: str) -> dict[str, Any]:
    now_sh = now_utc.astimezone(SH_TZ)
    root_html = session.get(root_url, timeout=30).text
    history_url = extract_waytoagi_history_url(root_html)

    root_client_vars = extract_feishu_client_vars(root_html)
    root_block_map = root_client_vars.get("data", {}).get("block_map", {})
    updates: list[dict[str, Any]] = extract_waytoagi_recent_updates_from_block_map(root_block_map, now_sh, root_url)

    if history_url and history_url != root_url:
        try:
            history_html = session.get(history_url, timeout=30).text
            history_client_vars = extract_feishu_client_vars(history_html)
            history_block_map = history_client_vars.get("data", {}).get("block_map", {})
            updates.extend(
                extract_waytoagi_recent_updates_from_block_map(history_block_map, now_sh, history_url)
            )
        except Exception as exc:
            logger.warning("WaytoAGI history page fetch failed: %s", exc)

    dedup_updates: dict[tuple[str, str], dict[str, Any]] = {}
    for item in updates:
        key = (str(item.get("date") or ""), str(item.get("title") or ""))
        if key[0] and key[1] and key not in dedup_updates:
            dedup_updates[key] = item

    start_date = now_sh.date() - timedelta(days=6)
    end_date = now_sh.date()
    recent = [
        u
        for u in dedup_updates.values()
        if start_date <= date.fromisoformat(str(u.get("date") or "1970-01-01")) <= end_date
    ]
    recent.sort(key=lambda x: (x["date"], x["title"]), reverse=True)
    latest_date = recent[0]["date"] if recent else None
    updates_today = [u for u in recent if u.get("date") == latest_date] if latest_date else []

    warning = "近7日未解析到更新条目" if not recent else None
    return {
        "generated_at": iso(now_utc),
        "timezone": "Asia/Shanghai",
        "root_url": root_url,
        "history_url": history_url,
        "window_days": 7,
        "latest_date": latest_date,
        "count_today": len(updates_today),
        "updates_today": updates_today,
        "count_7d": len(recent),
        "updates_7d": recent,
        "warning": warning,
        "has_error": False,
        "error": None,
    }

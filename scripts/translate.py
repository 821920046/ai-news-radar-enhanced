"""Title translation (EN->ZH) and bilingual field enrichment."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import requests

from scripts.utils import has_cjk, is_mostly_english, normalize_url

logger = logging.getLogger(__name__)


def load_title_zh_cache(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items() if str(k).strip() and str(v).strip()}
    except Exception:
        pass
    return {}


def translate_to_zh_cn(session: requests.Session, text: str) -> str | None:
    s = (text or "").strip()
    if not s:
        return None
    try:
        r = session.get(
            "https://translate.googleapis.com/translate_a/single",
            params={
                "client": "gtx",
                "sl": "auto",
                "tl": "zh-CN",
                "dt": "t",
                "q": s,
            },
            timeout=12,
        )
        r.raise_for_status()
        payload = r.json()
        if not isinstance(payload, list) or not payload:
            return None
        segs = payload[0]
        if not isinstance(segs, list):
            return None
        translated = "".join(str(seg[0]) for seg in segs if isinstance(seg, list) and seg and seg[0])
        translated = translated.strip()
        if translated and translated != s:
            return translated
    except Exception:
        return None
    return None


def add_bilingual_fields(
    items_ai: list[dict[str, Any]],
    items_all: list[dict[str, Any]],
    session: requests.Session,
    cache: dict[str, str],
    max_new_translations: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    zh_by_url: dict[str, str] = {}
    for it in items_all:
        title = str(it.get("title") or "").strip()
        url = normalize_url(str(it.get("url") or ""))
        if title and url and has_cjk(title):
            zh_by_url[url] = title

    translated_now = 0

    def enrich(item: dict[str, Any], allow_translate: bool) -> dict[str, Any]:
        nonlocal translated_now
        out = dict(item)
        title = str(out.get("title") or "").strip()
        url = normalize_url(str(out.get("url") or ""))

        out["title_original"] = title
        out["title_en"] = None
        out["title_zh"] = None
        out["title_bilingual"] = title

        if has_cjk(title):
            out["title_zh"] = title
            return out

        if not is_mostly_english(title):
            return out

        out["title_en"] = title

        zh_title = zh_by_url.get(url)
        if not zh_title:
            zh_title = cache.get(title)
        if not zh_title and allow_translate and translated_now < max_new_translations:
            tr = translate_to_zh_cn(session, title)
            if tr and has_cjk(tr):
                zh_title = tr
                cache[title] = tr
                translated_now += 1

        if zh_title:
            out["title_zh"] = zh_title
            out["title_bilingual"] = f"{zh_title} / {title}"
        return out

    ai_out = [enrich(it, allow_translate=True) for it in items_ai]
    all_out = [enrich(it, allow_translate=False) for it in items_all]
    return ai_out, all_out, cache

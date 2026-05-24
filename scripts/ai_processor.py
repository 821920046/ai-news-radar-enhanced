"""Optional OpenRouter-powered TL;DR generation for high-signal news items."""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import requests

logger = logging.getLogger(__name__)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_OPENROUTER_MODEL = "google/gemini-2.5-flash-free"
DEFAULT_TLDR_TOP_N = 10
DEFAULT_TLDR_MIN_CHARS = 30
DEFAULT_TLDR_MAX_WORKERS = 2


class KeyPoolManager:
    """Thread-safe round-robin API key pool with exhaustion tracking."""

    def __init__(self, keys_str: str):
        keys = [key.strip() for key in keys_str.split(",") if key.strip()]
        self.keys = list(dict.fromkeys(keys))
        self.current_index = 0
        self.exhausted_keys: set[str] = set()
        self._lock = threading.Lock()
        logger.info("[AI KeyPool] Initialized with %d API keys.", len(self.keys))

    def get_key(self) -> str | None:
        with self._lock:
            if not self.keys:
                return None
            for _ in range(len(self.keys)):
                key = self.keys[self.current_index]
                self.current_index = (self.current_index + 1) % len(self.keys)
                if key not in self.exhausted_keys:
                    return key
            return None

    def mark_exhausted(self, key: str) -> None:
        with self._lock:
            if key in self.exhausted_keys:
                return
            masked = f"{key[:6]}...{key[-4:]}" if len(key) > 10 else "short-key"
            logger.warning("[AI KeyPool] Key %s is exhausted or rate limited.", masked)
            self.exhausted_keys.add(key)

    def is_all_exhausted(self) -> bool:
        with self._lock:
            return bool(self.keys) and len(self.exhausted_keys) >= len(self.keys)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("[AI Pipeline] Invalid %s=%r; using %d.", name, raw, default)
        return default


def _is_disabled() -> bool:
    return os.environ.get("AI_TLDR_ENABLED", "").strip().lower() in {"0", "false", "no", "off"}


def _item_text(item: dict[str, Any]) -> str:
    fields = [
        item.get("title_zh"),
        item.get("title"),
        item.get("description"),
        item.get("source"),
        item.get("site_name"),
    ]
    text = " ".join(str(value).strip() for value in fields if value)
    return re.sub(r"\s+", " ", text).strip()


def _clean_tldr(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip(" \"'\n\t*-:："))
    text = re.sub(r"^(摘要|总结|TL;DR|Tldr|一句话新闻核心)\s*[:：]\s*", "", text, flags=re.I)
    return text[:80].strip()


def _selected_items(items: list[dict[str, Any]], limit: int, min_chars: int) -> list[dict[str, Any]]:
    candidates = [item for item in items if not item.get("tldr") and len(_item_text(item)) >= min_chars]
    candidates.sort(
        key=lambda item: (
            int(item.get("hotness_score") or 0),
            str(item.get("published_at") or item.get("first_seen_at") or ""),
        ),
        reverse=True,
    )
    if limit <= 0:
        return []
    return candidates[:limit]


def generate_tldr(
    text: str,
    key_manager: KeyPoolManager,
    *,
    session: requests.Session | None = None,
    model: str | None = None,
    timeout: int = 12,
) -> str:
    """Generate a concise Chinese TL;DR, returning an empty string on fallback."""
    if not text or len(text) < DEFAULT_TLDR_MIN_CHARS:
        return ""

    requester = session or requests
    model_name = model or os.environ.get("OPENROUTER_MODEL") or DEFAULT_OPENROUTER_MODEL
    referer = os.environ.get("OPENROUTER_HTTP_REFERER") or "https://github.com/LearnPrompt/ai-news-radar"
    app_title = os.environ.get("OPENROUTER_APP_TITLE") or "AI News Radar"

    system_prompt = (
        "你是极其干练的科技新闻主编。请把输入内容提炼成一句中文 TL;DR，"
        "不超过30个汉字。只输出结论本身，不要前缀、解释或项目符号。"
    )

    max_attempts = max(1, len(key_manager.keys))
    for attempt in range(max_attempts):
        key = key_manager.get_key()
        if not key:
            logger.warning("[AI] No usable OpenRouter keys remain.")
            return ""

        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": referer,
            "X-Title": app_title,
        }
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text[:1500]},
            ],
            "max_tokens": 80,
            "temperature": 0.2,
        }

        try:
            response = requester.post(OPENROUTER_API_URL, headers=headers, json=payload, timeout=timeout)
        except requests.exceptions.RequestException as exc:
            logger.warning("[AI] OpenRouter request failed on attempt %d: %s", attempt + 1, exc)
            time.sleep(min(2, attempt + 1))
            continue

        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError as exc:
                logger.warning("[AI] OpenRouter returned invalid JSON: %s", exc)
                return ""
            choices = data.get("choices") if isinstance(data, dict) else None
            if not choices:
                return ""
            content = choices[0].get("message", {}).get("content", "")
            return _clean_tldr(content)

        if response.status_code in {402, 403, 429}:
            key_manager.mark_exhausted(key)
            continue

        logger.error("[AI] OpenRouter API error %s: %s", response.status_code, response.text[:200])
        return ""

    return ""


def process_items_with_ai(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach `tldr` to the top configured items when OpenRouter keys exist."""
    if _is_disabled():
        logger.info("[AI Pipeline] AI_TLDR_ENABLED disables TL;DR generation.")
        return items

    keys_str = os.environ.get("OPENROUTER_KEYS", "")
    if not keys_str.strip():
        logger.info("[AI Pipeline] OPENROUTER_KEYS is not set; skipping TL;DR generation.")
        return items

    key_manager = KeyPoolManager(keys_str)
    if not key_manager.keys:
        return items

    min_chars = max(1, _env_int("AI_TLDR_MIN_CHARS", DEFAULT_TLDR_MIN_CHARS))
    top_n = _env_int("AI_TLDR_TOP_N", DEFAULT_TLDR_TOP_N)
    selected = _selected_items(items, top_n, min_chars)
    if not selected:
        logger.info("[AI Pipeline] No eligible items selected for TL;DR generation.")
        return items

    max_workers = max(1, _env_int("AI_TLDR_MAX_WORKERS", DEFAULT_TLDR_MAX_WORKERS))
    max_workers = min(max_workers, len(selected), len(key_manager.keys))
    logger.info("[AI Pipeline] Generating TL;DR for %d/%d items.", len(selected), len(items))

    def worker(item: dict[str, Any]) -> None:
        if key_manager.is_all_exhausted():
            return
        tldr = generate_tldr(_item_text(item), key_manager)
        if tldr:
            item["tldr"] = tldr

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        list(executor.map(worker, selected))

    return items

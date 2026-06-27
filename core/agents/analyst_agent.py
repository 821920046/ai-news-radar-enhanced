"""Analyst Agent: TL;DR generation + deep analysis via OpenRouter.

Migrated from scripts/ai_processor.py and enhanced with:
- KeyPoolManager: thread-safe round-robin API key pool
- generate_tldr(): concise Chinese TL;DR summaries
- deep_analyze(): 2-3 sentence significance analysis
- analyze_batch(): batch TL;DR + analysis for top items

Backward-compatible: standalone functions are preserved alongside
the AnalystAgent class wrapper.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import requests
from core.utils import _env_int, get_model_chain, mark_model_dead

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_OPENROUTER_MODEL = "deepseek/deepseek-chat-v3-0324:free"
DEFAULT_TLDR_TOP_N = 30
DEFAULT_TLDR_MIN_CHARS = 30
DEFAULT_TLDR_MAX_WORKERS = 2


# ---------------------------------------------------------------------------
# KeyPoolManager — thread-safe round-robin key rotation
# ---------------------------------------------------------------------------

class KeyPoolManager:
    """Thread-safe round-robin API key pool with exhaustion tracking."""

    def __init__(self, keys_str: str):
        keys = [key.strip() for key in keys_str.split(",") if key.strip()]
        self.keys = list(dict.fromkeys(keys))  # 去重保序
        self.current_index = 0
        self.exhausted_keys: set[str] = set()
        self._lock = threading.Lock()
        logger.info("[AI KeyPool] Initialized with %d API keys.", len(self.keys))

    def get_key(self) -> str | None:
        """获取一个可用的 API key，轮转选择。若无可用 key 返回 None。"""
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
        """将某个 key 标记为耗尽（限流/余额不足）。"""
        with self._lock:
            if key in self.exhausted_keys:
                return
            masked = f"{key[:6]}...{key[-4:]}" if len(key) > 10 else "short-key"
            logger.warning("[AI KeyPool] Key %s is exhausted or rate limited.", masked)
            self.exhausted_keys.add(key)

    def is_all_exhausted(self) -> bool:
        """所有 key 是否都已耗尽。"""
        with self._lock:
            return bool(self.keys) and len(self.exhausted_keys) >= len(self.keys)

    @property
    def available_count(self) -> int:
        """当前可用 key 数量。"""
        with self._lock:
            return len(self.keys) - len(self.exhausted_keys)


# ---------------------------------------------------------------------------
# Standalone helper functions (preserved for backward compatibility)
# ---------------------------------------------------------------------------

def _is_disabled() -> bool:
    """检查 AI_TLDR_ENABLED 环境变量是否关闭了 AI 分析。"""
    return os.environ.get("AI_TLDR_ENABLED", "").strip().lower() in {"0", "false", "no", "off"}


def _item_text(item: dict[str, Any]) -> str:
    """将文章的多个字段拼接为一段用于 AI 分析的文本。"""
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
    """清理 TL;DR 输出，去掉多余符号和前缀。"""
    text = re.sub(r"\s+", " ", (text or "").strip(" \"'\n\t*-:："))
    text = re.sub(
        r"^(摘要|总结|TL;DR|Tldr|一句话新闻核心|tl;dr)\s*[:：]\s*",
        "",
        text,
        flags=re.I,
    )
    return text[:80].strip()


def _parse_tldr_and_tags(content: str) -> tuple[str, list[str]]:
    """解析 AI 返回的 JSON 格式，提取 tldr 和 tags。支持防御性兜底。"""
    content = (content or "").strip()
    if not content:
        return "", []

    # 移除 markdown 代码块包裹 ```json ... ```
    if content.startswith("```"):
        match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", content, re.DOTALL)
        if match:
            content = match.group(1).strip()

    # 查找第一个 { 和最后一个 } 边界
    start_idx = content.find("{")
    end_idx = content.rfind("}")
    if start_idx != -1 and end_idx != -1 and start_idx < end_idx:
        json_str = content[start_idx:end_idx + 1]
        try:
            data = json.loads(json_str)
            if isinstance(data, dict):
                tldr = str(data.get("tldr") or "").strip()
                raw_tags = data.get("tags") or []
                tags = [str(t).strip() for t in raw_tags if str(t).strip()]
                return tldr, tags
        except Exception:
            pass

    # JSON 解析失败兜底，整段作为 tldr，tags 为空
    return _clean_tldr(content), []


def _selected_items(
    items: list[dict[str, Any]], limit: int, min_chars: int
) -> list[dict[str, Any]]:
    """从文章列表中选出最适合做 AI 分析的前 N 条。

    排序规则：按 hotness_score 降序，再按 published_at 降序。
    """
    candidates = [
        item
        for item in items
        if not item.get("tldr") and len(_item_text(item)) >= min_chars
    ]
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


# ---------------------------------------------------------------------------
# Core generation functions
# ---------------------------------------------------------------------------

def generate_tldr(
    text: str,
    key_manager: KeyPoolManager,
    *,
    session: requests.Session | None = None,
    model: str | None = None,
    timeout: int = 12,
    item: dict[str, Any] | None = None,
) -> str:
    """Generate a concise Chinese TL;DR via OpenRouter API.

    用极其干练的方式将文章内容提炼成一句不超过30个汉字的总结，并提取核心细分标签。
    失败时返回空字符串，不抛异常。

    Args:
        text: 待提炼的文章文本（会自动截断到 1500 字符）
        key_manager: API key 池管理器
        session: 可复用的 requests.Session
        model: 模型名，不传则用环境变量或默认值
        timeout: 单次请求超时秒数
        item: 可选的新闻条目字典，用于原地合并和更新提取出的 tags 标签
    """
    if not text or len(text) < DEFAULT_TLDR_MIN_CHARS:
        return ""

    requester = session or requests
    model_chain = get_model_chain(model)
    model_name = model_chain[0]
    referer = os.environ.get("OPENROUTER_HTTP_REFERER") or "https://github.com/LearnPrompt/ai-news-radar"
    app_title = os.environ.get("OPENROUTER_APP_TITLE") or "AI News Radar"

    system_prompt = (
        "你是极其干练的科技新闻主编。根据输入的新闻内容，返回一个 JSON 格式的对象，包含以下字段：\n"
        "1. tldr: 一句中文 TL;DR 总结，不超过 30 个汉字，不要有任何前缀或解释。\n"
        "2. tags: 包含 2-3 个最核心、最精准的中文细分标签的列表（如：'模型发布', 'AI编程', '开源', '自动驾驶' 等）。\n"
        "不要包含任何 markdown 代码块标记，只输出合法的 JSON 字符串。"
    )

    max_attempts = max(len(model_chain), len(key_manager.keys))
    for attempt in range(max_attempts):
        model_name = model_chain[attempt % len(model_chain)]
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
            "max_tokens": 150,
            "temperature": 0.2,
        }

        try:
            response = requester.post(
                OPENROUTER_API_URL, headers=headers, json=payload, timeout=timeout
            )
        except requests.exceptions.RequestException as exc:
            logger.warning(
                "[AI] OpenRouter request failed on attempt %d: %s", attempt + 1, exc
            )
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
            tldr, tags = _parse_tldr_and_tags(content)
            
            if item is not None and isinstance(item, dict) and tags:
                existing_tags = item.get("tags") or []
                combined_tags = []
                seen = set()
                # 优先保留 AI 标签并排序
                for t in tags:
                    if t not in seen:
                        combined_tags.append(t)
                        seen.add(t)
                for t in existing_tags:
                    if t not in seen:
                        combined_tags.append(t)
                        seen.add(t)
                item["tags"] = combined_tags[:4]  # 最多保留4个标签
                
            return tldr

        if response.status_code in {402, 403, 429}:
            # 402/403=账号额度/鉴权；429=免费额度限流（OpenRouter 免费档多为账号级共享限额）→ 换 key
            key_manager.mark_exhausted(key)
            continue

        if response.status_code in {400, 404}:
            # 模型名错误/已下线 → 标记后换下一个模型
            mark_model_dead(model_name)
            continue

        logger.error(
            "[AI] OpenRouter API error %s: %s",
            response.status_code,
            response.text[:200],
        )
        continue

    return ""


def deep_analyze(
    article: dict[str, Any],
    key_manager: KeyPoolManager,
    *,
    session: requests.Session | None = None,
    model: str | None = None,
    timeout: int = 20,
) -> str:
    """Generate a deeper 2-3 sentence analysis of an article's significance.

    与 TL;DR 不同，deep_analyze 旨在揭示事件对 AI 领域的深层含义、
    行业格局影响和值得留意的信号，而不是简单的摘要。

    Args:
        article: 文章数据字典
        key_manager: API key 池管理器
        session: 可复用的 requests.Session
        model: 模型名
        timeout: 单次请求超时秒数
    """
    text = _item_text(article)
    if not text or len(text) < DEFAULT_TLDR_MIN_CHARS:
        return ""

    requester = session or requests
    model_chain = get_model_chain(model)
    model_name = model_chain[0]
    referer = os.environ.get("OPENROUTER_HTTP_REFERER") or "https://github.com/LearnPrompt/ai-news-radar"
    app_title = os.environ.get("OPENROUTER_APP_TITLE") or "AI News Radar"

    system_prompt = (
        "你是资深 AI 产业分析师。请用 2-3 句中文分析以下新闻对 AI 行业的深层意义，"
        "关注点包括：它揭示了什么行业趋势？对开发者或创业者有什么启示？"
        "是否暗示了某种技术路线或商业模式的转变？"
        "只输出分析内容本身，不要前缀或解释。"
    )

    max_attempts = max(len(model_chain), len(key_manager.keys))
    for attempt in range(max_attempts):
        model_name = model_chain[attempt % len(model_chain)]
        key = key_manager.get_key()
        if not key:
            logger.warning("[AI Deep] No usable OpenRouter keys remain.")
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
                {"role": "user", "content": text[:2000]},
            ],
            "max_tokens": 200,
            "temperature": 0.3,
        }

        try:
            response = requester.post(
                OPENROUTER_API_URL, headers=headers, json=payload, timeout=timeout
            )
        except requests.exceptions.RequestException as exc:
            logger.warning(
                "[AI Deep] OpenRouter request failed on attempt %d: %s", attempt + 1, exc
            )
            time.sleep(min(2, attempt + 1))
            continue

        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError as exc:
                logger.warning("[AI Deep] OpenRouter returned invalid JSON: %s", exc)
                return ""
            choices = data.get("choices") if isinstance(data, dict) else None
            if not choices:
                return ""
            content = choices[0].get("message", {}).get("content", "")
            return content.strip()

        if response.status_code in {402, 403, 429}:
            # 402/403=账号额度/鉴权；429=免费额度限流（OpenRouter 免费档多为账号级共享限额）→ 换 key
            key_manager.mark_exhausted(key)
            continue

        if response.status_code in {400, 404}:
            # 模型名错误/已下线 → 标记后换下一个模型
            mark_model_dead(model_name)
            continue

        logger.error(
            "[AI Deep] OpenRouter API error %s: %s",
            response.status_code,
            response.text[:200],
        )
        continue

    return ""


# ---------------------------------------------------------------------------
# Batch processing (preserved for backward compatibility)
# ---------------------------------------------------------------------------

def process_items_with_ai(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach `tldr` to the top configured items when OpenRouter keys exist.

    这是原始的批量处理入口，保持不变以兼容现有调用方。
    新代码请使用 AnalystAgent.analyze_batch()。
    """
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

    min_chars = max(1, _env_int("AI_TLDR_MIN_CHARS", DEFAULT_TLDR_MIN_CHARS, prefix="AI Pipeline"))
    top_n = _env_int("AI_TLDR_TOP_N", DEFAULT_TLDR_TOP_N, prefix="AI Pipeline")
    selected = _selected_items(items, top_n, min_chars)
    if not selected:
        logger.info("[AI Pipeline] No eligible items selected for TL;DR generation.")
        return items

    max_workers = max(
        1, _env_int("AI_TLDR_MAX_WORKERS", DEFAULT_TLDR_MAX_WORKERS, prefix="AI Pipeline")
    )
    max_workers = min(max_workers, len(selected), len(key_manager.keys))
    logger.info("[AI Pipeline] Generating TL;DR for %d/%d items.", len(selected), len(items))

    def worker(item: dict[str, Any]) -> None:
        if key_manager.is_all_exhausted():
            return
        tldr = generate_tldr(_item_text(item), key_manager, item=item)
        if tldr:
            item["tldr"] = tldr

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        list(executor.map(worker, selected))

    return items


# ---------------------------------------------------------------------------
# AnalystAgent — class-based wrapper
# ---------------------------------------------------------------------------

class AnalystAgent:
    """Generates TL;DR summaries and deeper analysis for news articles.

    通过 OpenRouter API 为高信号文章生成中文 TL;DR 摘要和深度分析。
    兼容原有的 scripts/ai_processor.py 中的独立函数。
    """

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.default_model = (
            self.config.get("model")
            or os.environ.get("OPENROUTER_MODEL")
            or None
        )
        self.top_n = self.config.get("top_n") or int(
            os.environ.get("AI_TLDR_TOP_N", str(DEFAULT_TLDR_TOP_N))
        )
        self.min_chars = self.config.get("min_chars") or int(
            os.environ.get("AI_TLDR_MIN_CHARS", str(DEFAULT_TLDR_MIN_CHARS))
        )
        self.max_workers = self.config.get("max_workers") or int(
            os.environ.get("AI_TLDR_MAX_WORKERS", str(DEFAULT_TLDR_MAX_WORKERS))
        )
        self._key_manager: KeyPoolManager | None = None

    # ------------------------------------------------------------------
    # Key pool
    # ------------------------------------------------------------------

    def _get_key_manager(self) -> KeyPoolManager | None:
        """延迟初始化 KeyPoolManager（兼容环境变量在 import 后设置的情况）。"""
        if self._key_manager is not None:
            return self._key_manager

        keys_str = os.environ.get("OPENROUTER_KEYS", "")
        if not keys_str.strip():
            logger.info("[AnalystAgent] OPENROUTER_KEYS is not set.")
            return None

        self._key_manager = KeyPoolManager(keys_str)
        if not self._key_manager.keys:
            logger.warning("[AnalystAgent] Key pool is empty despite OPENROUTER_KEYS being set.")
            self._key_manager = None
        return self._key_manager

    def reset_key_pool(self) -> None:
        """重置 key pool，下次 _get_key_manager 会从环境变量重新读取。"""
        self._key_manager = None

    # ------------------------------------------------------------------
    # Feature gate
    # ------------------------------------------------------------------

    def is_enabled(self) -> bool:
        """Check if AI analysis is configured and enabled."""
        keys = os.environ.get("OPENROUTER_KEYS", "").strip()
        disabled = _is_disabled()
        return bool(keys) and not disabled

    # ------------------------------------------------------------------
    # TL;DR generation
    # ------------------------------------------------------------------

    def generate_tldr(
        self,
        text: str,
        *,
        session: requests.Session | None = None,
        model: str | None = None,
        timeout: int = 12,
        item: dict[str, Any] | None = None,
    ) -> str:
        """Generate a concise Chinese TL;DR for the given text."""
        key_manager = self._get_key_manager()
        if not key_manager:
            return ""
        return generate_tldr(
            text,
            key_manager,
            session=session,
            model=model or self.default_model,
            timeout=timeout,
            item=item,
        )

    # ------------------------------------------------------------------
    # Deep analysis
    # ------------------------------------------------------------------

    def deep_analyze(
        self,
        article: dict[str, Any],
        *,
        session: requests.Session | None = None,
        model: str | None = None,
        timeout: int = 20,
    ) -> str:
        """Generate a 2-3 sentence significance analysis for an article."""
        key_manager = self._get_key_manager()
        if not key_manager:
            return ""
        return deep_analyze(
            article,
            key_manager,
            session=session,
            model=model or self.default_model,
            timeout=timeout,
        )

    # ------------------------------------------------------------------
    # Batch processing
    # ------------------------------------------------------------------

    def analyze_batch(
        self,
        items: list[dict[str, Any]],
        *,
        include_deep: bool = False,
    ) -> list[dict[str, Any]]:
        """Attach tldr (and optionally deep analysis) to top items.

        Args:
            items: 文章列表（会被原地修改）
            include_deep: 是否同时生成 deep_analysis 字段

        Returns:
            原地修改后的 items 列表
        """
        if not self.is_enabled():
            logger.info("[AnalystAgent] AI analysis is disabled; skipping batch.")
            return items

        key_manager = self._get_key_manager()
        if not key_manager:
            return items

        selected = _selected_items(items, self.top_n, self.min_chars)
        if not selected:
            logger.info("[AnalystAgent] No eligible items for AI analysis.")
            return items

        workers = max(1, min(self.max_workers, len(selected), key_manager.available_count))
        logger.info(
            "[AnalystAgent] Processing %d items with %d workers "
            "(TL;DR%s)",
            len(selected),
            workers,
            " + deep analysis" if include_deep else "",
        )

        def worker(item: dict[str, Any]) -> None:
            if key_manager.is_all_exhausted():
                return
            # TL;DR + tags
            tldr = generate_tldr(_item_text(item), key_manager, item=item)
            if tldr:
                item["tldr"] = tldr
            # Deep analysis (only for high-scoring items)
            if include_deep and item.get("signal_level") in {"S", "A"}:
                if key_manager.is_all_exhausted():
                    return
                analysis = deep_analyze(item, key_manager)
                if analysis:
                    item["deep_analysis"] = analysis

        with ThreadPoolExecutor(max_workers=workers) as executor:
            list(executor.map(worker, selected))

        return items

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def item_text(self, item: dict[str, Any]) -> str:
        """Extract combined text from an article dict for AI processing."""
        return _item_text(item)

    @staticmethod
    def clean_tldr(text: str) -> str:
        """Clean a raw TL;DR string."""
        return _clean_tldr(text)

    @staticmethod
    def is_ai_disabled() -> bool:
        """Check the AI_TLDR_ENABLED env-var gate."""
        return _is_disabled()

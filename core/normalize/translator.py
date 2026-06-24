"""Title translation (EN->ZH) and bilingual field enrichment.

支持两种翻译后端：
1. OpenRouter AI 翻译（高质量，需要 OPENROUTER_KEYS）
2. Google Translate 免费 API（兜底方案）
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import requests

from core.utils import has_cjk, is_mostly_english, normalize_url

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OpenRouter AI 翻译配置
# ---------------------------------------------------------------------------

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_OPENROUTER_MODEL = "google/gemma-4-31b-it:free"

# AI 翻译的系统提示词：要求高质量、自然流畅的中文翻译
AI_TRANSLATE_SYSTEM_PROMPT = (
    "你是专业的中英科技新闻翻译官。将以下英文标题翻译成简洁准确的中文。"
    "要求：1) 保留专有名词原文（如 GPT-5、Claude、OpenAI）；"
    "2) 译文自然流畅，符合中文新闻标题习惯；"
    "3) 只输出翻译结果，不要任何解释或前缀。"
)

# 批量翻译的系统提示词
AI_BATCH_TRANSLATE_SYSTEM_PROMPT = (
    "你是专业的中英科技新闻翻译官。将以下编号的英文标题逐条翻译为简洁准确的中文。"
    "要求：1) 保留专有名词原文（如 GPT-5、Claude、OpenAI）；"
    "2) 译文自然流畅，符合中文新闻标题习惯；"
    "3) 严格按原编号输出，每行一条，格式：编号. 中文译文；"
    "4) 不要任何额外解释。"
)

# AI 翻译 description 的系统提示词
AI_DESC_TRANSLATE_SYSTEM_PROMPT = (
    "你是专业的科技新闻编辑。将以下英文文章描述翻译为精炼的中文摘要（一到两句话，不超过100字）。"
    "要求：1) 保留关键数据和专有名词；"
    "2) 译文自然流畅；3) 只输出翻译结果。"
)

# 批量翻译每批的上限
BATCH_SIZE = 8


# ---------------------------------------------------------------------------
# 翻译缓存管理
# ---------------------------------------------------------------------------

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


def safeguard_title_zh_cache(title_cache_path: Path, new_cache: dict[str, str]) -> None:
    """检查原缓存文件大小，若新生成的缓存出现断崖式暴跌，拒绝写入并生成备份。"""
    from core.utils import atomic_write_text

    if not title_cache_path.exists():
        return

    try:
        with open(title_cache_path, "r", encoding="utf-8") as old_f:
            old_cache = json.load(old_f)
    except Exception as e:
        logger.warning("Failed to load old cache for safeguard checks: %s", e)
        return

    if isinstance(old_cache, dict) and len(old_cache) > 100:
        if len(new_cache) < len(old_cache) * 0.5:
            bak_path = title_cache_path.with_suffix(".json.bak")
            try:
                atomic_write_text(bak_path, json.dumps(old_cache, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as e:
                logger.warning("Failed to write backup cache file: %s", e)
            raise ValueError(
                f"Translation cache data plummeted suspiciously from {len(old_cache)} to {len(new_cache)} entries! "
                f"Aborted write to protect translation data. Previous cache backed up to {bak_path}."
            )


# ---------------------------------------------------------------------------
# Google Translate 免费 API（兜底方案）
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# OpenRouter AI 翻译（高质量）
# ---------------------------------------------------------------------------

def _get_openrouter_keys() -> list[str]:
    """从环境变量获取 OpenRouter API keys。"""
    keys_str = os.environ.get("OPENROUTER_KEYS", "")
    return [k.strip() for k in keys_str.split(",") if k.strip()]


def _ai_translate_single(
    session: requests.Session,
    text: str,
    api_key: str,
    *,
    system_prompt: str = AI_TRANSLATE_SYSTEM_PROMPT,
    max_tokens: int = 120,
    timeout: int = 15,
) -> str | None:
    """通过 OpenRouter API 翻译单条文本。"""
    if not text or not api_key:
        return None

    model = os.environ.get("OPENROUTER_MODEL") or DEFAULT_OPENROUTER_MODEL
    referer = os.environ.get("OPENROUTER_HTTP_REFERER") or "https://github.com/LearnPrompt/ai-news-radar"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": referer,
        "X-Title": "AI News Radar",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text[:800]},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.1,
    }

    try:
        resp = session.post(OPENROUTER_API_URL, headers=headers, json=payload, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            choices = data.get("choices") if isinstance(data, dict) else None
            if choices:
                content = choices[0].get("message", {}).get("content", "").strip()
                # 清理常见前缀
                content = re.sub(r"^(翻译[：:]\s*|译文[：:]\s*)", "", content).strip()
                if content and has_cjk(content):
                    return content
        elif resp.status_code in {402, 403, 429}:
            logger.warning("[AI Translate] API key exhausted/rate-limited (HTTP %d)", resp.status_code)
            return None
        else:
            logger.warning("[AI Translate] Unexpected HTTP %d: %s", resp.status_code, resp.text[:200])
    except requests.exceptions.RequestException as exc:
        logger.warning("[AI Translate] Request failed: %s", exc)

    return None


def _ai_translate_batch(
    session: requests.Session,
    titles: list[str],
    api_key: str,
    *,
    timeout: int = 30,
) -> list[str | None]:
    """通过 OpenRouter API 批量翻译标题（一次 API 调用翻译多条）。"""
    if not titles or not api_key:
        return [None] * len(titles)

    model = os.environ.get("OPENROUTER_MODEL") or DEFAULT_OPENROUTER_MODEL
    referer = os.environ.get("OPENROUTER_HTTP_REFERER") or "https://github.com/LearnPrompt/ai-news-radar"

    # 构建编号列表
    numbered_input = "\n".join(f"{i + 1}. {title}" for i, title in enumerate(titles))

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": referer,
        "X-Title": "AI News Radar",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": AI_BATCH_TRANSLATE_SYSTEM_PROMPT},
            {"role": "user", "content": numbered_input},
        ],
        "max_tokens": 150 * len(titles),
        "temperature": 0.1,
    }

    try:
        resp = session.post(OPENROUTER_API_URL, headers=headers, json=payload, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            choices = data.get("choices") if isinstance(data, dict) else None
            if choices:
                content = choices[0].get("message", {}).get("content", "").strip()
                return _parse_batch_result(content, len(titles))
        elif resp.status_code in {402, 403, 429}:
            logger.warning("[AI Translate Batch] API key exhausted/rate-limited (HTTP %d)", resp.status_code)
        else:
            logger.warning("[AI Translate Batch] HTTP %d: %s", resp.status_code, resp.text[:200])
    except requests.exceptions.RequestException as exc:
        logger.warning("[AI Translate Batch] Request failed: %s", exc)

    return [None] * len(titles)


def _parse_batch_result(content: str, expected_count: int) -> list[str | None]:
    """解析批量翻译的编号结果。"""
    results: list[str | None] = [None] * expected_count
    lines = content.strip().split("\n")

    for line in lines:
        line = line.strip()
        if not line:
            continue
        # 匹配格式: "1. 翻译内容" 或 "1、翻译内容" 或 "1) 翻译内容"
        match = re.match(r"^(\d+)[.、)]\s*(.+)$", line)
        if match:
            idx = int(match.group(1)) - 1
            translated = match.group(2).strip()
            # 清理引号包裹
            translated = translated.strip("\"'「」『』")
            if 0 <= idx < expected_count and translated and has_cjk(translated):
                results[idx] = translated

    return results


def _ai_translate_description(
    session: requests.Session,
    desc: str,
    api_key: str,
    *,
    timeout: int = 20,
) -> str | None:
    """通过 OpenRouter 将英文 description 翻译为中文精炼摘要。"""
    return _ai_translate_single(
        session,
        desc,
        api_key,
        system_prompt=AI_DESC_TRANSLATE_SYSTEM_PROMPT,
        max_tokens=200,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# 核心入口：双语字段添加（整合 AI 翻译 + Google 翻译兜底）
# ---------------------------------------------------------------------------

def add_bilingual_fields(
    items_ai: list[dict[str, Any]],
    items_all: list[dict[str, Any]],
    session: requests.Session,
    cache: dict[str, str],
    max_new_translations: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    """为文章添加双语标题字段，优先使用 AI 翻译。"""

    # 收集已有的中文标题映射（URL → 中文标题）
    zh_by_url: dict[str, str] = {}
    for it in items_all:
        title = str(it.get("title") or "").strip()
        url = normalize_url(str(it.get("url") or ""))
        if title and url and has_cjk(title):
            zh_by_url[url] = title

    # 获取 OpenRouter API keys
    api_keys = _get_openrouter_keys()
    use_ai = bool(api_keys) and os.environ.get("AI_TRANSLATE_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
    current_key_idx = 0
    ai_translated_count = 0
    google_translated_count = 0

    if use_ai:
        logger.info("[Translate] AI 翻译已启用，共 %d 个 API key", len(api_keys))
    else:
        logger.info("[Translate] AI 翻译未启用，将使用 Google Translate 兜底")

    # 第一轮：收集需要翻译的英文标题
    pending_titles: list[tuple[dict[str, Any], str]] = []  # (item, english_title)

    def _needs_translation(item: dict[str, Any]) -> tuple[bool, str]:
        """检查是否需要翻译，返回 (需要翻译, 英文标题)。"""
        title = str(item.get("title") or "").strip()
        url = normalize_url(str(item.get("url") or ""))

        if has_cjk(title):
            return False, title

        if not is_mostly_english(title):
            return False, title

        # 检查缓存
        zh = zh_by_url.get(url) or cache.get(title)
        if zh:
            return False, title

        return True, title

    # 为 items_ai 收集需要翻译的条目
    for item in items_ai:
        needs, title = _needs_translation(item)
        if needs and len(pending_titles) < max_new_translations:
            pending_titles.append((item, title))

    # AI 批量翻译
    if use_ai and pending_titles:
        logger.info("[Translate] AI 批量翻译 %d 条标题...", len(pending_titles))

        for batch_start in range(0, len(pending_titles), BATCH_SIZE):
            batch = pending_titles[batch_start:batch_start + BATCH_SIZE]
            batch_titles = [title for _, title in batch]

            if current_key_idx >= len(api_keys):
                logger.warning("[Translate] 所有 API key 已耗尽，剩余 %d 条切换到 Google Translate",
                               len(pending_titles) - batch_start)
                break

            api_key = api_keys[current_key_idx]
            results = _ai_translate_batch(session, batch_titles, api_key, timeout=30)

            all_failed = all(r is None for r in results)
            if all_failed:
                current_key_idx += 1
                # 用下一个 key 重试
                if current_key_idx < len(api_keys):
                    api_key = api_keys[current_key_idx]
                    results = _ai_translate_batch(session, batch_titles, api_key, timeout=30)

            for i, (item, en_title) in enumerate(batch):
                zh_title = results[i] if i < len(results) else None
                if zh_title:
                    cache[en_title] = zh_title
                    ai_translated_count += 1

            # 控制请求频率，避免触发限流
            time.sleep(0.5)

    # 通用 enrich 函数（应用翻译结果 + Google 兜底）
    google_budget = max_new_translations - ai_translated_count

    def enrich(item: dict[str, Any], allow_translate: bool) -> dict[str, Any]:
        nonlocal google_budget, google_translated_count
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

        # 查找已有翻译
        zh_title = zh_by_url.get(url) or cache.get(title)

        # Google Translate 兜底
        if not zh_title and allow_translate and google_budget > 0:
            tr = translate_to_zh_cn(session, title)
            if tr and has_cjk(tr):
                zh_title = tr
                cache[title] = tr
                google_budget -= 1
                google_translated_count += 1

        if zh_title:
            out["title_zh"] = zh_title
            out["title_bilingual"] = f"{zh_title} / {title}"

        # 翻译 description（仅对 AI 模式条目且有 API key 时）
        if use_ai and allow_translate and api_keys:
            _try_translate_desc(session, out, api_keys, current_key_idx)

        return out

    ai_out = [enrich(it, allow_translate=True) for it in items_ai]
    all_out = [enrich(it, allow_translate=False) for it in items_all]

    logger.info(
        "[Translate] 翻译完成：AI 翻译 %d 条，Google 翻译 %d 条，缓存命中跳过其余",
        ai_translated_count, google_translated_count,
    )

    return ai_out, all_out, cache


def _try_translate_desc(
    session: requests.Session,
    item: dict[str, Any],
    api_keys: list[str],
    start_key_idx: int,
) -> None:
    """尝试将英文 description 翻译为中文（仅在没有 tldr 且 desc 是英文时触发）。"""
    desc = str(item.get("description") or "").strip()
    if not desc or has_cjk(desc) or len(desc) < 20:
        return

    # 只对没有 tldr 的条目翻译 description
    if item.get("tldr"):
        return

    if not is_mostly_english(desc):
        return

    for idx in range(start_key_idx, len(api_keys)):
        zh_desc = _ai_translate_description(session, desc, api_keys[idx], timeout=15)
        if zh_desc:
            item["description"] = zh_desc
            return

    # AI 翻译失败，用 Google Translate 兜底
    try:
        tr = translate_to_zh_cn(session, desc[:300])
        if tr and has_cjk(tr):
            item["description"] = tr
    except Exception:
        pass

"""AI topic filtering: keyword lists, signal detection, and content sanitization."""

from __future__ import annotations

import logging
import re
from typing import Any

from scripts.utils import normalize_url, host_of_url
from scripts.utils import maybe_fix_mojibake  # noqa: F401 (re-exported for callers that import from here)

logger = logging.getLogger(__name__)

AI_KEYWORDS = [
    "aigc",
    "llm",
    "gpt",
    "claude",
    "gemini",
    "deepseek",
    "openai",
    "anthropic",
    "copilot",
    "codex",
    "mcp",
    "hugging face",
    "huggingface",
    "transformer",
    "prompt",
    "diffusion",
    "agent",
    "多模态",
    "大模型",
    "模型",
    "人工智能",
    "机器学习",
    "深度学习",
    "智能体",
    "算力",
    "推理",
    "微调",
]

TECH_KEYWORDS = [
    "robot",
    "robotics",
    "embodied",
    "autonomous",
    "vision",
    "chip",
    "semiconductor",
    "cuda",
    "npu",
    "gpu",
    "cloud",
    "developer",
    "开源",
    "技术",
    "编程",
    "软件",
    "芯片",
    "机器人",
    "具身",
]

NOISE_KEYWORDS = [
    "娱乐",
    "明星",
    "八卦",
    "足球",
    "篮球",
    "彩票",
    "情感",
    "旅游",
    "美食",
]

COMMERCE_NOISE_KEYWORDS = [
    "淘宝",
    "天猫",
    "京东",
    "拼多多",
    "券后",
    "热销总榜",
    "促销",
    "优惠",
    "补贴",
    "下单",
    "首发价",
]

EN_SIGNAL_RE = re.compile(
    r"(?i)(?<![a-z0-9])(ai|aigc|llm|gpt|openai|anthropic|deepseek|gemini|claude|robot|robotics|embodied|autonomous|machine learning|artificial intelligence|transformer|diffusion|agent)(?![a-z0-9])"
)

TOPHUB_ALLOW_KEYWORDS = [
    "readhub · ai",
    "hacker news",
    "github",
    "product hunt",
    "v2ex",
    "少数派",
    "infoq",
    "36氪",
    "机器之心",
    "量子位",
    "科技",
    "人工智能",
    "机器人",
    "具身",
    "开源",
]

TOPHUB_BLOCK_KEYWORDS = [
    "热销总榜",
    "淘宝",
    "天猫",
    "京东",
    "拼多多",
    "抖音",
    "快手",
    "微博",
    "小红书",
]


MEANINGFUL_EN_SIGNAL_RE = re.compile(
    r"(?i)(?<![a-z0-9])(ai|aigc|llm|gpt|openai|anthropic|deepseek|gemini|claude|robot|robotics|embodied|autonomous|machine learning|artificial intelligence|transformer|diffusion)(?![a-z0-9])"
)
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
SECRET_LIKE_RE = re.compile(r"\b(sk-(?!hynix\b)[A-Za-z0-9_-]{12,}|(?:api[_-]?key|secret|token)=([^\s&]{6,}))\b", re.I)
BROAD_AI_TERMS = {"agent", "模型", "推理"}


def contains_any_keyword(haystack: str, keywords: list[str]) -> bool:
    h = haystack.lower()
    return any(k in h for k in keywords)


def contains_meaningful_ai_signal(haystack: str) -> bool:
    h = haystack.lower()
    if MEANINGFUL_EN_SIGNAL_RE.search(h):
        return True
    return any(k in h for k in AI_KEYWORDS if k not in BROAD_AI_TERMS)


def redact_public_text(text: str) -> str:
    if not isinstance(text, str) or not text:
        return text
    text = EMAIL_RE.sub("[redacted-email]", text)
    return SECRET_LIKE_RE.sub("[redacted-secret]", text)


def sanitize_public_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_public_text(value)
    if isinstance(value, list):
        return [sanitize_public_value(item) for item in value]
    if isinstance(value, dict):
        return {key: sanitize_public_value(val) for key, val in value.items()}
    return value


def sanitize_public_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return sanitize_public_value(payload)


def has_mojibake_noise(text: str) -> bool:
    if not text:
        return False
    return bool(re.search(r"(Ã|Â|â€|æ·|�)", text))


def normalize_source_for_display(site_id: str, source: str, url: str) -> str:
    src = (source or "").strip()
    if not src:
        host = host_of_url(url)
        if host.startswith("www."):
            host = host[4:]
        return host or "未分区"
    if site_id == "buzzing" and src.lower() == "buzzing":
        host = host_of_url(url)
        if host.startswith("www."):
            host = host[4:]
        return host or src
    return src


def is_ai_related_record(record: dict[str, Any]) -> bool:
    site_id = str(record.get("site_id") or "")
    title = str(record.get("title") or "")
    source = str(record.get("source") or "")
    site_name = str(record.get("site_name") or "")
    url = str(record.get("url") or "")
    text = f"{title} {source} {site_name} {url}".lower()

    # zeli: only keep Hacker News 24h hottest.
    if site_id == "zeli":
        return "24h" in source.lower() or "24h最热" in source

    if site_id == "tophub":
        source_l = source.lower()
        if has_mojibake_noise(source) or has_mojibake_noise(title):
            return False
        if contains_any_keyword(source_l, TOPHUB_BLOCK_KEYWORDS):
            return False
        if not contains_any_keyword(source_l, TOPHUB_ALLOW_KEYWORDS):
            return False

    # AI/hot aggregation sites are kept by default to avoid false negatives.
    if site_id in {"aibase", "aihot", "aihubtoday"}:
        return True

    has_ai = contains_meaningful_ai_signal(text)
    has_broad_ai = contains_any_keyword(text, list(BROAD_AI_TERMS)) or EN_SIGNAL_RE.search(text) is not None
    has_tech = contains_any_keyword(text, TECH_KEYWORDS)

    if not (has_ai or (has_broad_ai and has_tech)):
        return False

    if contains_any_keyword(text, COMMERCE_NOISE_KEYWORDS) and not has_ai:
        return False

    # Drop obvious noise when no clear AI signal is present.
    if contains_any_keyword(text, NOISE_KEYWORDS) and not has_ai:
        return False

    return True

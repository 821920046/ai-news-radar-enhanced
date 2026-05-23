"""AI topic filtering: keyword lists, signal detection, and content sanitization."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from scripts.utils import normalize_url, host_of_url
from scripts.utils import maybe_fix_mojibake  # noqa: F401 (re-exported for callers that import from here)

logger = logging.getLogger(__name__)

# ===========================================================================
# 默认内置词库 (Fallback) - 确保在配置缺失或损坏时程序仍能稳定运行
# ===========================================================================
_DEFAULT_AI_KEYWORDS = [
    "aigc", "llm", "gpt", "claude", "gemini", "deepseek", "openai", "anthropic",
    "copilot", "codex", "mcp", "hugging face", "huggingface", "transformer",
    "prompt", "diffusion", "agent", "多模态", "大模型", "模型", "人工智能",
    "机器学习", "深度学习", "智能体", "算力", "推理", "微调"
]

_DEFAULT_TECH_KEYWORDS = [
    "robot", "robotics", "embodied", "autonomous", "vision", "chip", "semiconductor",
    "cuda", "npu", "gpu", "cloud", "developer", "开源", "技术", "编程", "软件",
    "芯片", "机器人", "具身"
]

_DEFAULT_NOISE_KEYWORDS = [
    "娱乐", "明星", "八卦", "足球", "篮球", "彩票", "情感", "旅游", "美食"
]

_DEFAULT_COMMERCE_NOISE_KEYWORDS = [
    "淘宝", "天猫", "京东", "拼多多", "券后", "热销总榜", "促销", "优惠", "补贴",
    "下单", "首发价"
]

_DEFAULT_TOPHUB_ALLOW_KEYWORDS = [
    "readhub · ai", "hacker news", "github", "product hunt", "v2ex", "少数派",
    "infoq", "36氪", "机器之心", "量子位", "科技", "人工智能", "机器人", "具身", "开源"
]

_DEFAULT_TOPHUB_BLOCK_KEYWORDS = [
    "热销总榜", "淘宝", "天猫", "京东", "拼多多", "抖音", "快手", "微博", "小红书"
]

_DEFAULT_TOPIC_HARDWARE_KEYWORDS = [
    "cpu", "gpu", "显卡", "处理器", "主板", "内存", "ssd", "硬盘",
    "ryzen", "intel core", "geforce", "rtx", "radeon", "arc a",
    "ddr5", "ddr4", "pcie", "nvme", "散热", "电源", "机箱",
    "显示器", "笔记本电脑", "台式机", "pc ", "工作站",
    "chipset", "motherboard", "graphics card", "ram ",
    "qualcomm snapdragon", "mediatek dimensity",
    "苹果m4", "苹果m3", "apple m4", "apple m3"
]

_DEFAULT_TOPIC_DIGITAL_KEYWORDS = [
    "手机", "iphone", "ipad", "平板", "可穿戴", "耳机", "手表",
    "智能家居", "相机", "充电", "折叠屏", "智能手表",
    "ios", "android", "鸿蒙", "harmonyos",
    "小米", "华为", "三星", "oppo", "vivo", "荣耀",
    "samsung", "pixel", "oneplus", "realme",
    "tws", "降噪", "快充", "无线充电",
    "wearable", "smartphone", "tablet", "earbuds",
    "智能眼镜", "ar眼镜", "vr头显", "vision pro"
]

_DEFAULT_TOPIC_AI_KEYWORDS = [
    "llm", "gpt", "claude", "gemini", "openai", "anthropic",
    "deepseek", "copilot", "codex", "mcp", "huggingface",
    "hugging face", "transformer", "diffusion",
    "大模型", "人工智能", "机器学习", "深度学习",
    "智能体", "多模态", "算力", "推理引擎", "微调",
    "aigc", "chatgpt", "midjourney", "stable diffusion",
    "prompt", "fine-tune", "rag", "embedding",
    "langchain", "llamaindex", "ollama",
    "神经网络", "neural", "训练数据",
    "ai agent", "ai编程", "ai coding"
]

_DEFAULT_TOPIC_TECH_KEYWORDS = [
    "startup", "融资", "创业", "cloud", "saas",
    "开源", "开发者", "编程", "github", "product hunt",
    "软件", "互联网", "科技",
    "36氪", "少数派", "infoq", "sspai", "v2ex",
    "readhub", "hacker news",
    "自动驾驶", "量子计算", "区块链", "web3",
    "cybersecurity", "安全漏洞", "privacy", "隐私"
]

_DEFAULT_TAG_RULES = [
    ("智能体", ["agent", "智能体", "autonomous", "agentic", "multi-agent"]),
    ("模型发布", ["gpt", "claude", "gemini", "llama", "mistral", "qwen", "deepseek", "发布", "release", "launch", "announce"]),
    ("论文研究", ["paper", "arxiv", "论文", "研究", "benchmark", "测评", "survey", "research", "preprint"]),
    ("编码工具", ["copilot", "codex", "coding", "编程", "ide", "cursor", "vscode", "code assistant", "ai编程", "ai coding"]),
    ("MCP/工具", ["mcp", "tool use", "plugin", "插件", "工具", "function call", "tool calling"]),
    ("开源", ["open source", "开源", "github", "huggingface", "hugging face", "apache", "mit license"]),
    ("部署推理", ["inference", "部署", "推理", "serving", "onnx", "tensorrt", "ollama", "vllm", "gguf", "quantiz", "distill"]),
    ("多模态", ["multimodal", "多模态", "vision", "视觉", "image", "diffusion", "video", "audio", "speech", "whisper", "dall-e", "midjourney"]),
    ("安全对齐", ["safety", "alignment", "安全", "对齐", "red team", "rlhf", "guardrail", "responsible ai"]),
    ("行业动态", ["融资", "startup", "收购", "acquisition", "partnership", "估值", "valuation", "ipo", "投资"])
]

# ===========================================================================
# 规则动态加载
# ===========================================================================
def _load_rules_config() -> dict[str, Any]:
    config_path = Path(__file__).resolve().parent.parent / "configs" / "topic_rules.json"
    if config_path.exists():
        try:
            return json.loads(config_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Failed to load topic_rules.json: %s. Using default fallback rules.", e)
    return {}

_rules = _load_rules_config()

AI_KEYWORDS = _rules.get("AI_KEYWORDS", _DEFAULT_AI_KEYWORDS)
TECH_KEYWORDS = _rules.get("TECH_KEYWORDS", _DEFAULT_TECH_KEYWORDS)
NOISE_KEYWORDS = _rules.get("NOISE_KEYWORDS", _DEFAULT_NOISE_KEYWORDS)
COMMERCE_NOISE_KEYWORDS = _rules.get("COMMERCE_NOISE_KEYWORDS", _DEFAULT_COMMERCE_NOISE_KEYWORDS)
TOPHUB_ALLOW_KEYWORDS = _rules.get("TOPHUB_ALLOW_KEYWORDS", _DEFAULT_TOPHUB_ALLOW_KEYWORDS)
TOPHUB_BLOCK_KEYWORDS = _rules.get("TOPHUB_BLOCK_KEYWORDS", _DEFAULT_TOPHUB_BLOCK_KEYWORDS)

TOPIC_HARDWARE_KEYWORDS = _rules.get("TOPIC_HARDWARE_KEYWORDS", _DEFAULT_TOPIC_HARDWARE_KEYWORDS)
TOPIC_DIGITAL_KEYWORDS = _rules.get("TOPIC_DIGITAL_KEYWORDS", _DEFAULT_TOPIC_DIGITAL_KEYWORDS)
TOPIC_AI_KEYWORDS = _rules.get("TOPIC_AI_KEYWORDS", _DEFAULT_TOPIC_AI_KEYWORDS)
TOPIC_TECH_KEYWORDS = _rules.get("TOPIC_TECH_KEYWORDS", _DEFAULT_TOPIC_TECH_KEYWORDS)

# 转换 TAG_RULES 为 list[tuple[str, list[str]]]
raw_tag_rules = _rules.get("TAG_RULES")
if raw_tag_rules and isinstance(raw_tag_rules, list):
    TAG_RULES = []
    for item in raw_tag_rules:
        if isinstance(item, list) and len(item) == 2:
            TAG_RULES.append((str(item[0]), list(item[1])))
else:
    TAG_RULES = _DEFAULT_TAG_RULES


EN_SIGNAL_RE = re.compile(
    r"(?i)(?<![a-z0-9])(ai|aigc|llm|gpt|openai|anthropic|deepseek|gemini|claude|robot|robotics|embodied|autonomous|machine learning|artificial intelligence|transformer|diffusion|agent)(?![a-z0-9])"
)

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
    return bool(re.search(r"(Ã|Â|â€|æ·)", text))


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


# ---- Topic category classification ----
# 为每条新闻打上 topic_category 字段，用于前端分类导航栏
# 优先级：电脑硬件 > 数码 > AI > 科技（默认）

def classify_item(record: dict[str, Any]) -> str:
    """为新闻条目分配主题分类。返回值之一：'电脑硬件'/'数码'/'AI'/'科技'"""
    title = str(record.get("title") or "")
    source = str(record.get("source") or "")
    site_name = str(record.get("site_name") or "")
    text = f"{title} {source} {site_name}".lower()

    # 优先级匹配：硬件 > 数码 > AI > 科技（默认）
    if contains_any_keyword(text, TOPIC_HARDWARE_KEYWORDS):
        return "电脑硬件"
    if contains_any_keyword(text, TOPIC_DIGITAL_KEYWORDS):
        return "数码"
    if contains_any_keyword(text, TOPIC_AI_KEYWORDS) or contains_meaningful_ai_signal(text):
        return "AI"
    if contains_any_keyword(text, TOPIC_TECH_KEYWORDS):
        return "科技"
    # 兜底：没有匹配到任何关键词，默认归科技
    return "科技"


# ---- 多标签分类 ----
# 为每条新闻打上 0-3 个细分标签，用于前端标签行展示

def classify_tags(record: dict[str, Any]) -> list[str]:
    """为新闻条目返回 0-3 个细分标签。"""
    title = str(record.get("title") or "")
    source = str(record.get("source") or "")
    description = str(record.get("description") or "")
    text = f"{title} {source} {description}".lower()

    matched: list[str] = []
    for tag_name, keywords in TAG_RULES:
        if len(matched) >= 3:
            break
        if contains_any_keyword(text, keywords):
            matched.append(tag_name)
    return matched

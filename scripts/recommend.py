"""Presentation-oriented recommendation fields for reader-facing news cards."""

from __future__ import annotations

import re
from typing import Any

from scripts.utils import host_of_url, strip_html_tags


TAG_ANGLE_MAP = {
    "模型发布": "适合优先评估模型能力、价格和工具选型变化",
    "编码工具": "适合判断 AI 编程工作流会不会被改写",
    "论文研究": "适合判断技术路线和后续产品化可能性",
    "MCP/工具": "适合关注 Agent 工具调用和工作流搭建",
    "开源": "适合进一步验证代码、协议或社区采用情况",
    "部署推理": "适合观察推理部署成本和落地门槛变化",
    "多模态": "适合关注内容生产和交互形态的新变化",
    "安全对齐": "适合跟进 AI 风险、治理和可信边界",
    "行业动态": "适合判断资金、客户和生态格局的变化",
    "智能体": "适合关注自动化任务和多 Agent 协作演进",
}
DEFAULT_ANGLE = "适合作为今日 AI/科技情报流里的重点线索"
MAX_FACT_LEN = 58
MAX_REASON_LEN = 108
ENTITY_RE = re.compile(
    r"OpenRouter|OpenAI|Anthropic|Claude|Google DeepMind|DeepMind|Google|Microsoft|微软|"
    r"GitHub|Hugging Face|NVIDIA|英伟达|Meta|阿里|通义|Qwen[\w.-]*|DeepSeek[\w.-]*|"
    r"Gemini[\w.-]*|GPT[\w.-]*|Llama[\w.-]*|Mistral[\w.-]*|Cursor|Codex|"
    r"Hyper3D|Rodin[\w.-]*|Midjourney|Runway|Perplexity",
    re.I,
)
SIGNAL_NUMBER_RE = re.compile(
    r"(?:\$|约|超|超过|近)?\d+(?:\.\d+)?\s*(?:万亿|亿元|亿美元|万美元|美元|美金|"
    r"亿|万|%|分|小时|分钟|秒|天|token|tokens|Token|参数|B|M|K|倍|x)",
    re.I,
)


def _clean_text(value: Any) -> str:
    text = strip_html_tags(str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text.strip(" -_|,，。")


def _clip(text: str, max_len: int) -> str:
    text = _clean_text(text)
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip(" ,，、。") + "..."


def _display_title(record: dict[str, Any]) -> str:
    for key in ("title_zh", "title", "title_en", "title_bilingual"):
        text = _clean_text(record.get(key))
        if text:
            return text
    return ""


def _subject_from_title(title: str) -> str:
    matches: list[str] = []
    for match in ENTITY_RE.finditer(title):
        entity = match.group(0)
        if entity.lower() not in {m.lower() for m in matches}:
            matches.append(entity)
        if len(matches) >= 2:
            break
    if matches:
        return "、".join(matches)

    lead = re.sub(r"^[\[【][^\]】]{1,16}[\]】]\s*", "", title)
    lead = re.split(r"[：:，,。！？!?|｜]", lead, maxsplit=1)[0]
    lead = re.sub(r"\s+", " ", lead).strip()
    return _clip(lead, 20)


def _clause_with_signal(text: str) -> str:
    text = _clean_text(text)
    if not text:
        return ""
    clauses = [c.strip() for c in re.split(r"[。！？!?；;\n]", text) if c.strip()]
    for clause in clauses:
        if SIGNAL_NUMBER_RE.search(clause) or ENTITY_RE.search(clause):
            return _clip(clause, MAX_FACT_LEN)
    return _clip(clauses[0] if clauses else text, MAX_FACT_LEN)


def _fact_from_record(record: dict[str, Any], title: str) -> str:
    tldr = _clause_with_signal(str(record.get("tldr") or ""))
    if tldr:
        return tldr

    description = _clause_with_signal(str(record.get("description") or ""))
    if description:
        return description

    if "：" in title or ":" in title:
        fact = re.split(r"[：:]", title, maxsplit=1)[1]
        if fact:
            return _clip(fact, MAX_FACT_LEN)

    clauses = [c.strip() for c in re.split(r"[，,。！？!?；;]", title) if c.strip()]
    for clause in clauses:
        if SIGNAL_NUMBER_RE.search(clause):
            return _clip(clause, MAX_FACT_LEN)
    return _clip(title, MAX_FACT_LEN)


def _angle_for_tags(tags: list[str]) -> str:
    for tag in tags:
        if tag in TAG_ANGLE_MAP:
            return TAG_ANGLE_MAP[tag]
    return DEFAULT_ANGLE


def _finish_reason(reason: str) -> str:
    reason = _clean_text(reason).rstrip("，,。")
    if len(reason) > MAX_REASON_LEN:
        reason = reason[:MAX_REASON_LEN].rstrip("，,、。") + "..."
    return f"{reason}。"


def build_signal_score(record: dict[str, Any]) -> int:
    """Map mixed source signals into a compact 0-99 display score."""
    hotness = int(record.get("hotness_score") or 0)
    score = 58 + min(24, hotness // 25)

    site_id = str(record.get("site_id") or "")
    if site_id == "official_ai":
        score += 12
    elif site_id in {"aibreakfast", "followbuilders"}:
        score += 9
    elif site_id in {"zeli", "buzzing", "newsnow"}:
        score += 6

    tags = record.get("tags") or []
    score += min(9, len(tags) * 3)

    if record.get("tldr"):
        score += 3
    if record.get("image_url"):
        score += 2

    return max(60, min(99, score))


def build_recommendation_reason(record: dict[str, Any]) -> str:
    tags = [str(tag) for tag in record.get("tags") or []]
    title = _display_title(record)
    subject = _subject_from_title(title)
    fact = _fact_from_record(record, title)
    angle = _angle_for_tags(tags)
    site_id = str(record.get("site_id") or "")
    site_name = str(record.get("site_name") or "这个来源")
    hotness = int(record.get("hotness_score") or 0)

    if subject and fact and subject not in fact:
        return _finish_reason(f"{subject}：{fact}，{angle}")
    if fact:
        if site_id == "official_ai":
            return _finish_reason(f"{fact}，来自官方更新源，适合直接判断产品变化")
        if hotness >= 400:
            return _finish_reason(f"{fact}，同时进入热榜视野，{angle}")
        if site_id in {"aibreakfast", "followbuilders"}:
            return _finish_reason(f"{fact}，来自 {site_name} 精选源，{angle}")
        return _finish_reason(f"{fact}，{angle}")

    host = host_of_url(str(record.get("url") or ""))
    if host:
        return _finish_reason(f"来自 {host} 的新线索，{angle}")
    return _finish_reason(angle)


def enrich_recommendation_fields(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach recommendation reason and compact score in-place."""
    for item in items:
        item["recommendation_reason"] = build_recommendation_reason(item)
        item["signal_score"] = build_signal_score(item)
    return items

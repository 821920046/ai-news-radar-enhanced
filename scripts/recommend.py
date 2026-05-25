"""Presentation-oriented recommendation fields for reader-facing news cards."""

from __future__ import annotations

from typing import Any

from scripts.utils import host_of_url


TAG_REASON_MAP = {
    "模型发布": "涉及模型或产品发布，可能影响工具选型和后续生态，值得优先了解。",
    "编码工具": "和 AI 编程工具链直接相关，适合关注开发效率变化。",
    "论文研究": "包含论文、基准或研究进展，适合判断技术方向。",
    "MCP/工具": "涉及工具调用或 MCP 生态，可能影响 Agent 工作流搭建。",
    "开源": "有开源项目或代码线索，方便进一步验证和上手试用。",
    "部署推理": "聚焦推理、部署或性能优化，对落地成本判断有参考价值。",
    "多模态": "覆盖图像、视频、语音等多模态能力，适合观察下一波应用形态。",
    "安全对齐": "涉及 AI 安全、对齐或治理，适合关注风险边界变化。",
    "行业动态": "反映公司、融资或生态合作变化，适合判断行业走向。",
    "智能体": "和智能体能力或多 Agent 协作相关，适合关注自动化工作流演进。",
}


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
    for tag in tags:
        if tag in TAG_REASON_MAP:
            return TAG_REASON_MAP[tag]

    site_id = str(record.get("site_id") or "")
    site_name = str(record.get("site_name") or "这个来源")
    hotness = int(record.get("hotness_score") or 0)

    if site_id == "official_ai":
        return "来自官方更新源，信息链路短，适合作为判断产品变化的基准信号。"
    if hotness >= 400:
        return "热度较高且出现在技术社区视野中，适合快速判断是否形成趋势。"
    if site_id in {"aibreakfast", "followbuilders"}:
        return f"来自 {site_name} 的精选源，噪声较低，适合补充一线 builders 视角。"
    if record.get("description"):
        return "标题和摘要信息完整，能帮助你在打开原文前快速判断价值。"

    host = host_of_url(str(record.get("url") or ""))
    if host:
        return f"来自 {host} 的新线索，已通过 AI/科技主题过滤，适合快速扫一眼。"
    return "已通过 AI/科技主题过滤，适合作为今日情报流的补充线索。"


def enrich_recommendation_fields(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach recommendation reason and compact score in-place."""
    for item in items:
        item["recommendation_reason"] = build_recommendation_reason(item)
        item["signal_score"] = build_signal_score(item)
    return items

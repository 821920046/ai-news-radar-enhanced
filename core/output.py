"""Output payload generation (splitting initial vs. all-mode payloads)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def build_latest_payloads(latest_payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split initial AI payload from bulky all-mode lists for lazy browser loading."""
    slim_payload = dict(latest_payload)
    all_payload = {
        "generated_at": latest_payload.get("generated_at"),
        "window_hours": latest_payload.get("window_hours"),
        "topic_filter": latest_payload.get("topic_filter"),
        "total_items_raw": latest_payload.get("total_items_raw"),
        "total_items_all_mode": latest_payload.get("total_items_all_mode"),
        "items_all": latest_payload.get("items_all", []),
    }
    slim_payload.pop("items_all", None)
    slim_payload["all_mode_data_url"] = "data/latest-24h-all.json"

    # Strip fields no longer needed (images removed, empty descriptions)
    _strip_item_fields(slim_payload.get("items", []))
    _strip_item_fields(slim_payload.get("items_ai", []))
    _strip_item_fields(all_payload.get("items_all", []))

    return slim_payload, all_payload


def _strip_item_fields(items: list[dict[str, Any]]) -> None:
    """Remove image_url, title_original, last_seen_at, and other redundant fields from items to reduce JSON payload."""
    for item in items:
        # 1. 移除图片 url 字段
        item.pop("image_url", None)
        
        # 2. 移除空描述
        if not item.get("description"):
            item.pop("description", None)
            
        # 3. 移除前端无用的 title_original
        item.pop("title_original", None)
        
        # 4. 移除前端无用的 last_seen_at
        item.pop("last_seen_at", None)

        # 5. 移除前端无用的 id 与 title_bilingual 字段
        item.pop("id", None)
        item.pop("title_bilingual", None)

        # 6. 双语标题冗余优化：若 title_en 与 title_zh 完全一致，则置空 title_en
        t_en = item.get("title_en")
        t_zh = item.get("title_zh")
        if t_en and t_zh and str(t_en).strip() == str(t_zh).strip():
            item["title_en"] = None

        # 7. 若已有 AI 生成的极简摘要 tldr，则剥离原始 description 字段以节省 Payload 空间
        if item.get("tldr"):
            item.pop("description", None)

        # 8. 其它合并信源精简：仅保留前端徽章及直链渲染所需的核心字段
        merged_sources = item.get("merged_sources")
        if isinstance(merged_sources, list):
            pruned = []
            for src in merged_sources:
                if isinstance(src, dict):
                    pruned.append({
                        "site_name": src.get("site_name"),
                        "source": src.get("source"),
                        "url": src.get("url")
                    })
            item["merged_sources"] = pruned

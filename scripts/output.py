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
    return slim_payload, all_payload

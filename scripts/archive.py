"""Archive management: loading and event_time helper."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from scripts.utils import event_time, parse_iso  # noqa: F401 (re-exported for callers)

logger = logging.getLogger(__name__)


def load_archive(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    items = payload.get("items", [])
    out: dict[str, dict[str, Any]] = {}
    if isinstance(items, list):
        for it in items:
            item_id = it.get("id")
            if item_id:
                out[item_id] = it
    elif isinstance(items, dict):
        for item_id, it in items.items():
            if isinstance(it, dict):
                it["id"] = item_id
                out[item_id] = it
    return out

"""Fetch Agent: orchestrates data collection from all sources."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from core.utils import create_session, utc_now

logger = logging.getLogger(__name__)


class FetchAgent:
    """Agent responsible for coordinating data collection from all news sources.

    Delegates the actual fetching to core.fetch.collect_all, and maintains
    a local status snapshot for diagnostics.
    """

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self._status_path = Path(self.config.get("status_path", "data/source-status.json"))

    # ------------------------------------------------------------------
    # 数据收集
    # ------------------------------------------------------------------

    def collect(
        self, session: requests.Session | None = None
    ) -> tuple[list[Any], list[dict]]:
        """Collect news from all sources.

        Delegates to core.fetch.collect_all, which returns a tuple of
        (raw_items, statuses) where raw_items is a list of RawItem and
        statuses is a list of per-source status dicts.

        Returns:
            (raw_items: list[RawItem], statuses: list[dict])
        """
        from core.fetch import collect_all

        sess = session or create_session()
        now = utc_now()
        logger.info("[FetchAgent] Starting collection at %s", now.isoformat())
        raw_items, statuses = collect_all(sess, now)
        logger.info(
            "[FetchAgent] Collection complete: %d items from %d sources",
            len(raw_items),
            len(statuses),
        )
        return raw_items, statuses

    # ------------------------------------------------------------------
    # 信源状态
    # ------------------------------------------------------------------

    def get_source_status(self) -> list[dict]:
        """Return last known status of all sources.

        Reads from data/source-status.json if it exists; returns an empty
        list otherwise.
        """
        if not self._status_path.exists():
            logger.debug("[FetchAgent] No source-status file at %s", self._status_path)
            return []

        try:
            payload = json.loads(self._status_path.read_text(encoding="utf-8"))
            sites = payload.get("sites", [])
            logger.debug("[FetchAgent] Loaded status for %d sources", len(sites))
            return sites
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("[FetchAgent] Failed to read source status: %s", exc)
            return []

    # ------------------------------------------------------------------
    # 便捷方法
    # ------------------------------------------------------------------

    def healthy_source_count(self) -> int:
        """Count sources whose last fetch succeeded (HTTP 2xx)."""
        return sum(
            1 for s in self.get_source_status()
            if str(s.get("status", "")).startswith("2")
        )

    def source_summary(self) -> dict[str, int]:
        """Return a summary dict of {total, ok, fail} source counts."""
        statuses = self.get_source_status()
        total = len(statuses)
        ok = sum(
            1 for s in statuses
            if str(s.get("status", "")).startswith("2")
        )
        return {"total": total, "ok": ok, "fail": total - ok}

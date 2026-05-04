"""Fetcher orchestrator: concurrent collection of all source fetchers."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

import requests

from scripts.models import RawItem

from scripts.fetchers.official import fetch_official_ai_updates
from scripts.fetchers.newsletters import fetch_ai_breakfast, fetch_bestblogs
from scripts.fetchers.builders import fetch_follow_builders
from scripts.fetchers.aggregators import (
    fetch_techurls,
    fetch_buzzing,
    fetch_iris,
    fetch_tophub,
    fetch_zeli,
    fetch_aihot,
    fetch_newsnow,
)
from scripts.fetchers.aihub import fetch_ai_hubtoday, fetch_aibase

logger = logging.getLogger(__name__)


def collect_all(session: requests.Session, now: datetime) -> tuple[list[RawItem], list[dict[str, Any]]]:
    tasks = [
        ("official_ai", "Official AI Updates", fetch_official_ai_updates),
        ("aibreakfast", "AI Breakfast", fetch_ai_breakfast),
        ("followbuilders", "Follow Builders", fetch_follow_builders),
        ("techurls", "TechURLs", fetch_techurls),
        ("buzzing", "Buzzing", fetch_buzzing),
        ("iris", "Info Flow", fetch_iris),
        ("bestblogs", "BestBlogs", fetch_bestblogs),
        ("tophub", "TopHub", fetch_tophub),
        ("zeli", "Zeli", fetch_zeli),
        ("aihubtoday", "AI HubToday", fetch_ai_hubtoday),
        ("aibase", "AIbase", fetch_aibase),
        ("newsnow", "NewsNow", fetch_newsnow),
    ]

    raw_items: list[RawItem] = []
    statuses: list[dict[str, Any]] = []

    def run_task(site_id: str, site_name: str, fn: Any) -> tuple[list[RawItem], dict[str, Any]]:
        start = time.perf_counter()
        error = None
        count = 0
        items: list[RawItem] = []
        try:
            items = fn(session, now)
            count = len(items)
        except Exception as exc:
            logger.warning("%s fetcher failed: %s", site_id, exc)
            error = str(exc)
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        status = {
            "site_id": site_id,
            "site_name": site_name,
            "ok": error is None,
            "item_count": count,
            "duration_ms": elapsed_ms,
            "error": error,
        }
        return items, status

    with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        futures = [executor.submit(run_task, site_id, site_name, fn) for site_id, site_name, fn in tasks]
        for future in as_completed(futures):
            items, status = future.result()
            raw_items.extend(items)
            statuses.append(status)

    return raw_items, statuses

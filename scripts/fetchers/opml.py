"""OPML RSS fetcher: parses OPML subscriptions and fetches RSS feeds."""

from __future__ import annotations

import hashlib
import logging
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

try:
    import feedparser
except ModuleNotFoundError:
    feedparser = None

from scripts.models import BROWSER_UA, RSS_FEED_REPLACEMENTS, RSS_FEED_SKIP_EXACT, RSS_FEED_SKIP_PREFIXES, RawItem
from scripts.utils import (
    create_session,
    extract_image_url_from_feed_entry,
    first_non_empty,
    host_of_url,
    parse_date_any,
    parse_feed_entries_via_xml,
    truncate_description,
)

logger = logging.getLogger(__name__)


def parse_opml_subscriptions(opml_path: Path) -> list[dict[str, str]]:
    root = ET.parse(opml_path).getroot()
    out: list[dict[str, str]] = []
    seen: set[str] = set()

    for outline in root.findall(".//outline"):
        xml_url = str(outline.attrib.get("xmlUrl") or "").strip()
        if not xml_url:
            continue
        if xml_url in seen:
            continue
        seen.add(xml_url)
        title = first_non_empty(
            outline.attrib.get("title"),
            outline.attrib.get("text"),
            host_of_url(xml_url),
            xml_url,
        )
        html_url = str(outline.attrib.get("htmlUrl") or "").strip()
        out.append(
            {
                "title": title,
                "xml_url": xml_url,
                "html_url": html_url,
            }
        )
    return out


def resolve_official_rss_url(feed_url: str) -> tuple[str | None, str | None]:
    src = (feed_url or "").strip()
    if not src:
        return None, "empty_url"
    if src in RSS_FEED_SKIP_EXACT:
        return None, "no_official_rss_or_unreachable"
    for prefix in RSS_FEED_SKIP_PREFIXES:
        if src.startswith(prefix):
            return None, "no_official_rss_for_source_type"
    replaced = RSS_FEED_REPLACEMENTS.get(src)
    if replaced:
        return replaced, "official_replacement"
    return src, None


def fetch_opml_rss(
    now: datetime,
    opml_path: Path,
    max_feeds: int = 0,
    session: requests.Session | None = None,
) -> tuple[list[RawItem], dict[str, Any], list[dict[str, Any]]]:
    """Fetch all feeds from an OPML file using a shared session with retry.

    Parameters
    ----------
    now : datetime
        Current UTC time for date parsing.
    opml_path : Path
        Path to the OPML file.
    max_feeds : int
        Optional cap on the number of feeds to fetch (0 = all).
    session : requests.Session | None
        Shared session with retry configuration. If None, a new session is
        created internally via ``create_session()``.
    """
    if session is None:
        session = create_session()

    feeds = parse_opml_subscriptions(opml_path)
    if max_feeds > 0:
        feeds = feeds[:max_feeds]

    out: list[RawItem] = []
    feed_statuses: list[dict[str, Any]] = []
    resolved_feeds: list[dict[str, str]] = []

    for feed in feeds:
        original_url = feed["xml_url"]
        resolved_url, skip_reason = resolve_official_rss_url(original_url)
        if not resolved_url:
            feed_id = hashlib.sha1(original_url.encode("utf-8")).hexdigest()[:10]
            feed_statuses.append(
                {
                    "site_id": f"opmlrss:{feed_id}",
                    "site_name": "OPML RSS",
                    "feed_title": feed["title"],
                    "feed_url": original_url,
                    "effective_feed_url": None,
                    "ok": True,
                    "item_count": 0,
                    "duration_ms": 0,
                    "error": None,
                    "skipped": True,
                    "skip_reason": skip_reason or "skipped",
                    "replaced": False,
                }
            )
            continue
        record = dict(feed)
        record["xml_url_original"] = original_url
        record["xml_url"] = resolved_url
        record["replaced"] = bool(resolved_url != original_url)
        resolved_feeds.append(record)

    def fetch_single_feed(feed: dict[str, str]) -> tuple[list[RawItem], dict[str, Any]]:
        feed_url = feed["xml_url"]
        original_feed_url = str(feed.get("xml_url_original") or feed_url)
        feed_title = feed["title"]
        feed_id = hashlib.sha1(feed_url.encode("utf-8")).hexdigest()[:10]
        start = time.perf_counter()
        error = None
        local_items: list[RawItem] = []

        try:
            # BUG FIX: use session.get() instead of requests.get() to benefit
            # from the retry configuration set up in create_session().
            resp = session.get(
                feed_url,
                timeout=12,
                headers={
                    "User-Agent": BROWSER_UA,
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
            )
            resp.raise_for_status()

            if feedparser is not None:
                parsed = feedparser.parse(resp.content)
                source_name = first_non_empty(
                    feed_title,
                    getattr(parsed, "feed", {}).get("title"),
                    host_of_url(feed_url),
                )
                entries = parsed.entries
                for entry in entries:
                    title = str(entry.get("title", "")).strip()
                    link = str(entry.get("link", "")).strip()
                    if not title or not link:
                        continue
                    published = (
                        parse_date_any(entry.get("published"), now)
                        or parse_date_any(entry.get("updated"), now)
                        or parse_date_any(entry.get("pubDate"), now)
                    )
                    if not published:
                        continue
                    raw_desc = str(
                        entry.get("summary")
                        or entry.get("description")
                        or (entry.get("content", [{}])[0].get("value") if entry.get("content") else "")
                        or ""
                    )
                    description = truncate_description(raw_desc) if raw_desc.strip() else ""
                    image_url = extract_image_url_from_feed_entry(entry, link)
                    local_items.append(
                        RawItem(
                            site_id="opmlrss",
                            site_name="OPML RSS",
                            source=source_name,
                            title=title,
                            url=link,
                            published_at=published,
                            meta={
                                "feed_url": feed_url,
                                "feed_home": feed.get("html_url") or "",
                                "image_url": image_url,
                            },
                            description=description,
                        )
                    )
            else:
                source_name = first_non_empty(feed_title, host_of_url(feed_url))
                entries = parse_feed_entries_via_xml(resp.content)
                for entry in entries:
                    published = parse_date_any(entry.get("published"), now)
                    if not published:
                        continue
                    local_items.append(
                        RawItem(
                            site_id="opmlrss",
                            site_name="OPML RSS",
                            source=source_name,
                            title=entry.get("title", ""),
                            url=entry.get("link", ""),
                            published_at=published,
                            meta={
                                "feed_url": feed_url,
                                "feed_home": feed.get("html_url") or "",
                                "image_url": entry.get("image_url", ""),
                            },
                            description=entry.get("description", ""),
                        )
                    )
        except Exception as exc:
            logger.warning("OPML RSS feed %s failed: %s", feed_url, exc)
            error = str(exc)

        duration_ms = int((time.perf_counter() - start) * 1000)
        status = {
            "site_id": f"opmlrss:{feed_id}",
            "site_name": "OPML RSS",
            "feed_title": feed_title,
            "feed_url": original_feed_url,
            "effective_feed_url": feed_url,
            "ok": error is None,
            "item_count": len(local_items),
            "duration_ms": duration_ms,
            "error": error,
            "skipped": False,
            "skip_reason": None,
            "replaced": bool(original_feed_url != feed_url),
        }
        return local_items, status

    if resolved_feeds:
        worker_count = min(20, max(4, len(resolved_feeds)))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(fetch_single_feed, feed) for feed in resolved_feeds]
            for future in as_completed(futures):
                items, status = future.result()
                out.extend(items)
                feed_statuses.append(status)

    feed_statuses.sort(key=lambda x: str(x.get("feed_title") or x.get("feed_url") or ""))
    total_duration_ms = sum(int(s.get("duration_ms") or 0) for s in feed_statuses)
    ok_feeds = sum(1 for s in feed_statuses if s["ok"])
    failed_feeds = sum(1 for s in feed_statuses if not s["ok"])
    skipped_feeds = sum(1 for s in feed_statuses if s.get("skipped"))
    replaced_feeds = sum(1 for s in feed_statuses if s.get("replaced"))

    summary_status = {
        "site_id": "opmlrss",
        "site_name": "OPML RSS",
        "ok": ok_feeds > 0,
        "partial_failures": failed_feeds,
        "item_count": len(out),
        "duration_ms": total_duration_ms,
        "error": None if failed_feeds == 0 else f"{failed_feeds} feeds failed",
        "feed_count": len(feeds),
        "effective_feed_count": len(resolved_feeds),
        "ok_feed_count": ok_feeds,
        "failed_feed_count": failed_feeds,
        "skipped_feed_count": skipped_feeds,
        "replaced_feed_count": replaced_feeds,
    }
    return out, summary_status, feed_statuses

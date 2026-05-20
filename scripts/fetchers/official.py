"""Official AI source fetchers: Anthropic, OpenAI, and curated RSS feeds."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

try:
    import feedparser
except ModuleNotFoundError:
    feedparser = None

from scripts.models import BROWSER_UA, OFFICIAL_AI_FEEDS, OFFICIAL_AI_MAX_AGE_DAYS, RawItem
from scripts.utils import maybe_fix_mojibake, parse_date_any, parse_feed_entries_via_xml, truncate_description

logger = logging.getLogger(__name__)


def parse_anthropic_news_items(page_html: str, now: datetime) -> list[RawItem]:
    site_id = "official_ai"
    site_name = "Official AI Updates"
    soup = BeautifulSoup(page_html, "html.parser")
    out: list[RawItem] = []
    seen: set[str] = set()

    for a in soup.select('a[href^="/news/"]'):
        href = str(a.get("href") or "").strip()
        if not href or href == "/news/" or href == "/news":
            continue

        title_tag = a.select_one("h1, h2, h3, h4")
        title = title_tag.get_text(" ", strip=True) if title_tag else ""
        title = maybe_fix_mojibake(title)
        if not title or title.lower() == "news":
            continue

        url = urljoin("https://www.anthropic.com", href)
        if url in seen:
            continue
        seen.add(url)

        time_tag = a.select_one("time")
        published = None
        if time_tag:
            published = parse_date_any(time_tag.get("datetime") or time_tag.get_text(" ", strip=True), now)
        if not published:
            continue
        if now and published < now - timedelta(days=OFFICIAL_AI_MAX_AGE_DAYS):
            continue

        out.append(
            RawItem(
                site_id=site_id,
                site_name=site_name,
                source="Anthropic News",
                title=title,
                url=url,
                published_at=published,
                meta={"provider": "Anthropic"},
            )
        )

    return out


def parse_openai_codex_changelog_items(page_html: str, now: datetime) -> list[RawItem]:
    site_id = "official_ai"
    site_name = "Official AI Updates"
    soup = BeautifulSoup(page_html, "html.parser")
    out: list[RawItem] = []
    seen: set[str] = set()

    for node in soup.select("#codex-changelog-content li[id], li[id]"):
        item_id = str(node.get("id") or "").strip()
        if not item_id or item_id in seen:
            continue

        time_tag = node.select_one("time")
        title_tag = node.select_one("h3")
        if not time_tag or not title_tag:
            continue

        title = maybe_fix_mojibake(title_tag.get_text(" ", strip=True))
        published = parse_date_any(time_tag.get("datetime") or time_tag.get_text(" ", strip=True), now)
        if not title or not published:
            continue
        if now and published < now - timedelta(days=OFFICIAL_AI_MAX_AGE_DAYS):
            continue

        seen.add(item_id)
        out.append(
            RawItem(
                site_id=site_id,
                site_name=site_name,
                source="OpenAI Codex Changelog",
                title=title,
                url=f"https://developers.openai.com/codex/changelog#{item_id}",
                published_at=published,
                meta={"provider": "OpenAI"},
            )
        )

    return out


def fetch_feed_as_official_items(
    session: requests.Session,
    feed: dict[str, str],
    now: datetime,
) -> list[RawItem]:
    site_id = "official_ai"
    site_name = "Official AI Updates"
    feed_url = feed["xml_url"]
    feed_title = feed["title"]

    resp = session.get(
        feed_url,
        timeout=20,
        headers={
            "User-Agent": BROWSER_UA,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        },
    )
    resp.raise_for_status()

    entries: list[dict[str, Any]]
    if feedparser is not None:
        parsed = feedparser.parse(resp.content)
        entries = list(parsed.entries)
    else:
        entries = parse_feed_entries_via_xml(resp.content)

    out: list[RawItem] = []
    include_keywords = [
        keyword.strip().lower()
        for keyword in str(feed.get("include_keywords") or "").split(",")
        if keyword.strip()
    ]
    for entry in entries:
        title = str(entry.get("title", "")).strip()
        link = str(entry.get("link", "")).strip()
        if not title or not link:
            continue
        if include_keywords:
            haystack = f"{title} {link}".lower()
            if not any(keyword in haystack for keyword in include_keywords):
                continue
        published = (
            parse_date_any(entry.get("published"), now)
            or parse_date_any(entry.get("updated"), now)
            or parse_date_any(entry.get("pubDate"), now)
        )
        if not published:
            continue
        if published < now - timedelta(days=OFFICIAL_AI_MAX_AGE_DAYS):
            continue

        # Extract description from feedparser entry
        raw_desc = str(
            entry.get("summary")
            or entry.get("description")
            or (entry.get("content", [{}])[0].get("value") if entry.get("content") else "")
            or ""
        )
        description = truncate_description(raw_desc) if raw_desc.strip() else ""

        out.append(
            RawItem(
                site_id=site_id,
                site_name=site_name,
                source=feed_title,
                title=maybe_fix_mojibake(title),
                url=link,
                published_at=published,
                meta={
                    "feed_url": feed_url,
                    "feed_home": feed.get("html_url") or "",
                },
                description=description,
            )
        )

    return out


def fetch_official_ai_updates(session: requests.Session, now: datetime) -> list[RawItem]:
    out: list[RawItem] = []
    tasks = []

    # 1. 25+ RSS Feeds
    for feed in OFFICIAL_AI_FEEDS:
        tasks.append(
            (
                f"RSS: {feed.get('title')}",
                lambda f=feed: fetch_feed_as_official_items(session, f, now),
            )
        )

    # 2. Anthropic News Page
    def fetch_anthropic():
        r = session.get("https://www.anthropic.com/news", timeout=20)
        r.raise_for_status()
        return parse_anthropic_news_items(r.text, now)

    tasks.append(("Page: Anthropic News", fetch_anthropic))

    # 3. OpenAI Codex Changelog
    def fetch_openai():
        r = session.get("https://developers.openai.com/codex/changelog", timeout=20)
        r.raise_for_status()
        return parse_openai_codex_changelog_items(r.text, now)

    tasks.append(("Page: OpenAI Codex Changelog", fetch_openai))

    # 并发请求，最慢的源超时时间为 20s
    with ThreadPoolExecutor(max_workers=min(32, len(tasks))) as executor:
        future_to_name = {executor.submit(fn): name for name, fn in tasks}
        for future in as_completed(future_to_name):
            name = future_to_name[future]
            try:
                items = future.result()
                out.extend(items)
            except Exception as exc:
                logger.warning("Official source %s failed: %s", name, exc)

    if not out:
        raise ValueError("No official AI update sources returned items")

    return out

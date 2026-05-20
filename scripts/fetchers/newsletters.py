"""Newsletter fetchers: AI Breakfast, BestBlogs."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scripts.models import AIBREAKFAST_JINA_URL, BROWSER_UA, OFFICIAL_AI_MAX_AGE_DAYS, RawItem
from scripts.utils import maybe_fix_mojibake, parse_date_any, parse_unix_timestamp

logger = logging.getLogger(__name__)


def parse_ai_breakfast_items(markdown_text: str, now: datetime) -> list[RawItem]:
    site_id = "aibreakfast"
    site_name = "AI Breakfast"
    out: list[RawItem] = []
    seen: set[str] = set()
    pattern = re.compile(
        r"([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})\s+•\s+\d+\s+min read\s+###\s+\*\*(.*?)\*\*.*?"
        r"\]\((https?://aibreakfast\.beehiiv\.com/p/[^)]+)\)",
        re.S,
    )

    for date_text, title_text, url in pattern.findall(markdown_text or ""):
        url = url.strip()
        if not url or url in seen:
            continue
        published = parse_date_any(date_text, now)
        if not published:
            continue
        if now and published < now - timedelta(days=OFFICIAL_AI_MAX_AGE_DAYS):
            continue

        seen.add(url)
        title = re.sub(r"\s+", " ", title_text).strip()
        out.append(
            RawItem(
                site_id=site_id,
                site_name=site_name,
                source="AI Breakfast",
                title=maybe_fix_mojibake(title),
                url=url,
                published_at=published,
                meta={"feed_home": "https://aibreakfast.beehiiv.com/"},
            )
        )

    return out


def fetch_ai_breakfast(session: requests.Session, now: datetime) -> list[RawItem]:
    resp = session.get(
        AIBREAKFAST_JINA_URL,
        timeout=25,
        headers={
            "User-Agent": BROWSER_UA,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept": "text/plain, */*",
        },
    )
    resp.raise_for_status()
    out = parse_ai_breakfast_items(resp.text, now)
    if not out:
        raise ValueError("No AI Breakfast items parsed")
    return out


def fetch_bestblogs(session: requests.Session, now: datetime) -> list[RawItem]:
    site_id = "bestblogs"
    site_name = "BestBlogs"

    api = "https://api.bestblogs.dev/api/newsletter/list"
    out: list[RawItem] = []
    seen: set[str] = set()

    try:
        current_page = 1
        page_count = 1

        while current_page <= page_count and current_page <= 2:
            payload = {
                "currentPage": current_page,
                "pageSize": 20,
                "userLanguage": "en",
            }
            r = session.post(api, json=payload, timeout=30)
            r.raise_for_status()
            body = r.json()
            data = body.get("data", {})
            page_count = int(data.get("pageCount", 1) or 1)

            for issue in data.get("dataList", []):
                issue_id = str(issue.get("id", "")).strip()
                title = str(issue.get("title", "")).strip()
                if not issue_id or not title:
                    continue
                url = f"https://www.bestblogs.dev/en/newsletter#{issue_id}"
                if url in seen:
                    continue
                seen.add(url)

                published = parse_unix_timestamp(issue.get("createdTimestamp"))
                out.append(
                    RawItem(
                        site_id=site_id,
                        site_name=site_name,
                        source="Weekly Newsletter",
                        title=title,
                        url=url,
                        published_at=published,
                        meta={
                            "issue_id": issue_id,
                            "article_count": issue.get("articleCount"),
                        },
                    )
                )
            current_page += 1
    except Exception as exc:
        logger.warning("BestBlogs API fetch failed: %s", exc)

    if out:
        return out

    r = session.get("https://www.bestblogs.dev/en/newsletter", timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    for a in soup.select("a[href*='/newsletter']"):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        url = href if href.startswith("http") else urljoin("https://www.bestblogs.dev", href)
        title = a.get_text(" ", strip=True)
        if len(title) < 8:
            continue
        if url in seen:
            continue
        seen.add(url)
        dt = None
        time_tag = a.select_one("time")
        if time_tag:
            dt = parse_date_any(time_tag.get("datetime") or time_tag.get_text(" ", strip=True), now)
        out.append(
            RawItem(
                site_id=site_id,
                site_name=site_name,
                source="Weekly Newsletter",
                title=title,
                url=url,
                published_at=dt,
                meta={},
            )
        )

    return out

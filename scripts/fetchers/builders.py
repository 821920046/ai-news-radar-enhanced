"""Builder feed fetchers: Follow Builders (X, blogs, podcasts)."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

import requests

from scripts.models import BROWSER_UA, FOLLOW_BUILDERS_FEED_BASE, RawItem
from scripts.utils import maybe_fix_mojibake, parse_date_any

logger = logging.getLogger(__name__)


def parse_follow_builders_items(feeds: dict[str, dict[str, Any]], now: datetime) -> list[RawItem]:
    site_id = "followbuilders"
    site_name = "Follow Builders"
    out: list[RawItem] = []

    for builder in feeds.get("x", {}).get("x", []) or []:
        name = str(builder.get("name") or builder.get("handle") or "").strip()
        handle = str(builder.get("handle") or "").strip()
        source = f"Follow Builders · X · {name or handle}".strip(" ·")
        for tweet in builder.get("tweets", []) or []:
            text = str(tweet.get("text") or "").strip()
            url = str(tweet.get("url") or "").strip()
            published = parse_date_any(tweet.get("createdAt"), now)
            if not text or not url or not published:
                continue
            title = re.sub(r"\s+", " ", text)
            if len(title) > 220:
                title = title[:217].rstrip() + "..."
            out.append(
                RawItem(
                    site_id=site_id,
                    site_name=site_name,
                    source=source,
                    title=maybe_fix_mojibake(title),
                    url=url,
                    published_at=published,
                    meta={"handle": handle, "feed": "feed-x.json"},
                )
            )

    for article in feeds.get("blogs", {}).get("blogs", []) or []:
        title = str(article.get("title") or "").strip()
        url = str(article.get("url") or "").strip()
        published = parse_date_any(article.get("publishedAt"), now) or parse_date_any(
            feeds.get("blogs", {}).get("generatedAt"), now
        )
        if not title or not url or not published:
            continue
        out.append(
            RawItem(
                site_id=site_id,
                site_name=site_name,
                source=f"Follow Builders · Blog · {article.get('name') or 'Blog'}",
                title=maybe_fix_mojibake(title),
                url=url,
                published_at=published,
                meta={"feed": "feed-blogs.json"},
            )
        )

    for episode in feeds.get("podcasts", {}).get("podcasts", []) or []:
        title = str(episode.get("title") or "").strip()
        url = str(episode.get("url") or "").strip()
        published = parse_date_any(episode.get("publishedAt"), now) or parse_date_any(
            feeds.get("podcasts", {}).get("generatedAt"), now
        )
        if not title or not url or not published:
            continue
        out.append(
            RawItem(
                site_id=site_id,
                site_name=site_name,
                source=f"Follow Builders · Podcast · {episode.get('name') or 'Podcast'}",
                title=maybe_fix_mojibake(title),
                url=url,
                published_at=published,
                meta={"feed": "feed-podcasts.json"},
            )
        )

    return out


def fetch_follow_builders(session: requests.Session, now: datetime) -> list[RawItem]:
    feeds: dict[str, dict[str, Any]] = {}
    for key, filename in (
        ("x", "feed-x.json"),
        ("blogs", "feed-blogs.json"),
        ("podcasts", "feed-podcasts.json"),
    ):
        resp = session.get(
            f"{FOLLOW_BUILDERS_FEED_BASE}/{filename}",
            timeout=20,
            headers={
                "User-Agent": BROWSER_UA,
                "Accept": "application/json, */*",
            },
        )
        resp.raise_for_status()
        feeds[key] = resp.json()

    out = parse_follow_builders_items(feeds, now)
    if not out:
        raise ValueError("No Follow Builders items parsed")
    return out

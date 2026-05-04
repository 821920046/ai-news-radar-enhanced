"""Aggregator source fetchers: TechURLs, Buzzing, Iris, TopHub, Zeli, AIhot, NewsNow."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

try:
    import feedparser
except ModuleNotFoundError:
    feedparser = None

from scripts.models import BROWSER_UA, RawItem
from scripts.utils import (
    first_non_empty,
    host_of_url,
    maybe_fix_mojibake,
    normalize_url,
    parse_date_any,
    parse_feed_entries_via_xml,
    parse_relative_time_zh,
    parse_unix_timestamp,
)

logger = logging.getLogger(__name__)


def extract_next_f_merged(html: str) -> str:
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', html, re.S)
    if not chunks:
        return ""
    merged = "".join(chunks)
    try:
        return bytes(merged, "utf-8").decode("unicode_escape")
    except Exception:
        return merged


def extract_balanced_json(decoded: str, key: str) -> Any:
    idx = decoded.find(key)
    if idx == -1:
        raise ValueError(f"Key not found: {key}")

    start = idx + len(key)
    while start < len(decoded) and decoded[start] != ":":
        start += 1
    start += 1
    while start < len(decoded) and decoded[start] not in "[{":
        start += 1

    open_ch = decoded[start]
    close_ch = "}" if open_ch == "{" else "]"
    depth = 0
    in_str = False
    esc = False
    end = None

    for i, ch in enumerate(decoded[start:], start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break

    if end is None:
        raise ValueError(f"Cannot parse JSON block for key: {key}")

    snippet = decoded[start:end]
    snippet = snippet.replace("$undefined", "null")
    snippet = re.sub(r'"\$D([^\"]+)"', r'"\1"', snippet)
    return json.loads(snippet)


def extract_next_data_payload(html: str) -> dict[str, Any] | None:
    m = re.search(
        r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>\s*(\{.*?\})\s*</script>',
        html,
        re.S,
    )
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def fetch_techurls(session: requests.Session, now: datetime) -> list[RawItem]:
    site_id = "techurls"
    site_name = "TechURLs"
    r = session.get("https://techurls.com/", timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    out: list[RawItem] = []
    for block in soup.select("div.publisher-block"):
        primary = (
            block.select_one(".publisher-text .primary").get_text(strip=True)
            if block.select_one(".publisher-text .primary")
            else block.get("data-publisher", "unknown")
        )
        secondary = (
            block.select_one(".publisher-text .secondary").get_text(strip=True)
            if block.select_one(".publisher-text .secondary")
            else ""
        )
        source = f"{primary} · {secondary}" if secondary and secondary != primary else primary

        for link_row in block.select("div.publisher-link"):
            a = link_row.select_one("a.article-link")
            if not a or not a.get("href"):
                continue
            title = a.get_text(" ", strip=True)
            url = a["href"].strip()

            time_hint = ""
            aside = link_row.select_one(".aside .text")
            if aside:
                time_hint = aside.get("title", "") or aside.get_text(" ", strip=True)

            published = parse_date_any(time_hint, now)
            out.append(
                RawItem(
                    site_id=site_id,
                    site_name=site_name,
                    source=source,
                    title=title,
                    url=url,
                    published_at=published,
                    meta={"time_hint": time_hint},
                )
            )

    return out


def fetch_buzzing(session: requests.Session, now: datetime) -> list[RawItem]:
    site_id = "buzzing"
    site_name = "Buzzing"
    r = session.get("https://www.buzzing.cc/feed.json", timeout=30)
    r.raise_for_status()
    payload = r.json()
    items = payload.get("items", [])

    out: list[RawItem] = []
    for it in items:
        title = (it.get("title") or "").strip()
        url = (it.get("url") or "").strip()
        if not title or not url:
            continue
        source = first_non_empty(
            it.get("source"),
            it.get("site_name"),
            it.get("channel"),
            it.get("category"),
            host_of_url(url),
            site_name,
        )
        published = parse_date_any(it.get("date_published") or it.get("date_modified"), now)
        out.append(
            RawItem(
                site_id=site_id,
                site_name=site_name,
                source=source,
                title=title,
                url=url,
                published_at=published,
                meta={"raw": {k: it.get(k) for k in ("source", "site_name", "channel", "category")}},
            )
        )
    return out


def fetch_iris(session: requests.Session, now: datetime) -> list[RawItem]:
    site_id = "iris"
    site_name = "Info Flow"

    r = session.get("https://iris.findtruman.io/web/info_flow", timeout=30)
    r.raise_for_status()
    html = r.text

    m = re.search(r"const\s+feeds\s*=\s*\[(.*?)\]\s*;", html, re.S)
    if not m:
        return []

    section = m.group(1)
    feeds = re.findall(
        r"\{\s*name:\s*'([^']+)'\s*,\s*url:\s*'([^']+)'\s*\}",
        section,
        re.S,
    )

    out: list[RawItem] = []
    for feed_name, feed_url in feeds:
        try:
            if feedparser is not None:
                parsed = feedparser.parse(feed_url)
                source_name = str(feed_name or getattr(parsed, "feed", {}).get("title") or "Iris Feed")
                for entry in parsed.entries:
                    title = str(entry.get("title", "")).strip()
                    url = str(entry.get("link", "")).strip()
                    if not title or not url:
                        continue
                    published = (
                        parse_date_any(entry.get("published"), now)
                        or parse_date_any(entry.get("updated"), now)
                        or parse_date_any(entry.get("pubDate"), now)
                    )
                    out.append(
                        RawItem(
                            site_id=site_id,
                            site_name=site_name,
                            source=source_name,
                            title=title,
                            url=url,
                            published_at=published,
                            meta={"feed_url": feed_url},
                        )
                    )
                continue

            feed_resp = session.get(feed_url, timeout=30)
            feed_resp.raise_for_status()
            entries = parse_feed_entries_via_xml(feed_resp.content)
            source_name = str(feed_name or "Iris Feed")
            for entry in entries:
                out.append(
                    RawItem(
                        site_id=site_id,
                        site_name=site_name,
                        source=source_name,
                        title=entry["title"],
                        url=entry["link"],
                        published_at=parse_date_any(entry.get("published"), now),
                        meta={"feed_url": feed_url},
                    )
                )
        except Exception as exc:
            logger.warning("Iris feed %s failed: %s", feed_url, exc)
            # Skip blocked/broken sub feeds and keep remaining feeds.
            continue
    return out


def fetch_tophub(session: requests.Session, now: datetime) -> list[RawItem]:
    site_id = "tophub"
    site_name = "TopHub"

    r = session.get("https://tophub.today/", timeout=30)
    r.raise_for_status()
    html = r.content.decode("utf-8", errors="replace")
    if "�" in html:
        for enc in ("gb18030", "utf-8"):
            try:
                candidate = r.content.decode(enc, errors="replace")
                if candidate.count("�") < html.count("�"):
                    html = candidate
            except Exception:
                continue
    soup = BeautifulSoup(html, "html.parser")

    out: list[RawItem] = []
    for block in soup.select(".cc-cd"):
        source_name_tag = block.select_one(".cc-cd-lb span")
        board_tag = block.select_one(".cc-cd-sb-st")
        source_name = source_name_tag.get_text(" ", strip=True) if source_name_tag else "TopHub"
        board_name = board_tag.get_text(" ", strip=True) if board_tag else ""
        source_name = maybe_fix_mojibake(source_name)
        board_name = maybe_fix_mojibake(board_name)
        source = f"{source_name} · {board_name}" if board_name else source_name

        for a in block.select(".cc-cd-cb-l a"):
            href = a.get("href", "").strip()
            row = a.select_one(".cc-cd-cb-ll")
            title_tag = row.select_one(".t") if row else None
            metric_tag = row.select_one(".e") if row else None

            title = (
                title_tag.get_text(" ", strip=True)
                if title_tag
                else a.get_text(" ", strip=True)
            )
            title = maybe_fix_mojibake(title)
            if not title or not href:
                continue

            full_url = href if href.startswith("http") else urljoin("https://tophub.today", href)
            row_text = row.get_text(" ", strip=True) if row else title
            published = parse_relative_time_zh(row_text, now)

            out.append(
                RawItem(
                    site_id=site_id,
                    site_name=site_name,
                    source=source,
                    title=title,
                    url=full_url,
                    published_at=published,
                    meta={"metric": metric_tag.get_text(" ", strip=True) if metric_tag else ""},
                )
            )

    return out


def fetch_zeli(session: requests.Session, now: datetime) -> list[RawItem]:
    site_id = "zeli"
    site_name = "Zeli"
    out: list[RawItem] = []

    url = "https://zeli.app/api/hacker-news?type=hot24h"
    r = session.get(url, timeout=30)
    r.raise_for_status()
    body = r.json()
    posts = body.get("posts", [])
    for p in posts:
        title = str(p.get("title", "")).strip()
        link = str(p.get("url", "")).strip()
        if not title or not link:
            continue
        published = parse_unix_timestamp(p.get("time")) or now
        out.append(
            RawItem(
                site_id=site_id,
                site_name=site_name,
                source="Hacker News · 24h最热",
                title=title,
                url=link,
                published_at=published,
                meta={"hn_id": p.get("id")},
            )
        )

    return out


def fetch_aihot(session: requests.Session, now: datetime) -> list[RawItem]:
    site_id = "aihot"
    site_name = "AI今日热榜"

    r = session.get("https://aihot.today/", timeout=30)
    r.raise_for_status()
    initial_data = None
    source_list = None

    decoded = extract_next_f_merged(r.text)
    if decoded:
        try:
            initial_data = extract_balanced_json(decoded, "initialDataMap")
            source_list = extract_balanced_json(decoded, "dataSources")
        except Exception as exc:
            logger.warning("AIhot extract_balanced_json failed: %s", exc)
            initial_data = None
            source_list = None

    if initial_data is None or source_list is None:
        next_data = extract_next_data_payload(r.text) or {}
        page_props = (
            next_data.get("props", {})
            .get("pageProps", {})
        )
        if isinstance(page_props.get("initialDataMap"), dict):
            initial_data = page_props.get("initialDataMap")
        if isinstance(page_props.get("dataSources"), list):
            source_list = page_props.get("dataSources")

    if initial_data is None or source_list is None:
        return []

    source_map = {str(s.get("id")): s.get("title", str(s.get("id"))) for s in source_list if isinstance(s, dict)}

    out: list[RawItem] = []
    for source_id, items in initial_data.items():
        source_name = maybe_fix_mojibake(source_map.get(str(source_id), str(source_id)))
        if not isinstance(items, list):
            continue
        for item in items:
            title = maybe_fix_mojibake(str(item.get("title_trans") or item.get("title") or "").strip())
            link = str(item.get("link") or "").strip()
            if not title or not link:
                continue
            published = parse_date_any(item.get("publish_time"), now) or now
            out.append(
                RawItem(
                    site_id=site_id,
                    site_name=site_name,
                    source=source_name,
                    title=title,
                    url=link,
                    published_at=published,
                    meta={"raw_source_id": source_id},
                )
            )

    return out


def extract_newsnow_source_ids(js: str) -> list[str]:
    marker = "{v2ex:vL"
    start = js.find(marker)
    if start == -1:
        return ["hackernews", "producthunt", "github", "sspai", "juejin", "36kr"]

    # Locate beginning "{" and parse until matching "}"
    block_start = start
    depth = 0
    end = None
    in_str = False
    esc = False

    for i, ch in enumerate(js[block_start:], block_start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue

        if ch == '"':
            in_str = True
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    if end is None:
        return ["hackernews", "producthunt", "github", "sspai", "juejin", "36kr"]

    obj = js[block_start:end]
    all_keys = [m.group(2) for m in re.finditer(r'(["\']?)([a-zA-Z0-9_-]+)\1\s*:', obj)]

    ignore = {
        "name",
        "column",
        "home",
        "https",
        "color",
        "interval",
        "title",
        "type",
        "redirect",
        "desc",
    }

    source_ids: list[str] = []
    for key in all_keys:
        if key in ignore:
            continue
        if key not in source_ids:
            source_ids.append(key)

    # API currently returns around 57 source ids successfully.
    return source_ids


def fetch_newsnow(session: requests.Session, now: datetime) -> list[RawItem]:
    site_id = "newsnow"
    site_name = "NewsNow"

    home = session.get("https://newsnow.busiyi.world/", timeout=30)
    home.raise_for_status()
    soup = BeautifulSoup(home.text, "html.parser")

    bundle = None
    for script in soup.select("script[src]"):
        src = script.get("src", "")
        if "/assets/index-" in src and src.endswith(".js"):
            bundle = urljoin("https://newsnow.busiyi.world/", src)
            break

    source_ids = ["hackernews", "producthunt", "github", "sspai", "juejin", "36kr"]
    if bundle:
        js = session.get(bundle, timeout=30).text
        source_ids = extract_newsnow_source_ids(js)

    headers = {
        "User-Agent": BROWSER_UA,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://newsnow.busiyi.world",
        "Referer": "https://newsnow.busiyi.world/",
    }

    response = session.post(
        "https://newsnow.busiyi.world/api/s/entire",
        json={"sources": source_ids},
        headers=headers,
        timeout=45,
    )

    if response.status_code != 200:
        # fallback to per-source API
        source_blocks = []
        for sid in source_ids:
            rr = session.get(f"https://newsnow.busiyi.world/api/s?id={sid}", headers=headers, timeout=20)
            if rr.status_code == 200:
                try:
                    source_blocks.append(rr.json())
                except Exception as exc:
                    logger.warning("NewsNow source %s JSON parse failed: %s", sid, exc)
                    pass
    else:
        body = response.json()
        source_blocks = body.get("data") if isinstance(body, dict) else body
    if not isinstance(source_blocks, list):
        source_blocks = []

    out: list[RawItem] = []
    for block in source_blocks:
        sid = str(block.get("id") or "unknown")
        source_title = first_non_empty(block.get("title"), block.get("name"), block.get("desc"), sid)
        source_label = f"{source_title} ({sid})" if source_title != sid else sid
        updated = parse_unix_timestamp(block.get("updatedTime")) or now
        items = block.get("items") or []
        for it in items:
            title = str(it.get("title") or "").strip()
            url = str(it.get("url") or "").strip()
            if not title or not url:
                continue

            published = None
            published = published or parse_date_any(it.get("pubDate"), now)
            if not published:
                extra = it.get("extra") or {}
                if isinstance(extra, dict):
                    published = parse_date_any(extra.get("date"), now)
            if not published:
                published = updated

            out.append(
                RawItem(
                    site_id=site_id,
                    site_name=site_name,
                    source=source_label,
                    title=title,
                    url=url,
                    published_at=published,
                    meta={},
                )
            )

    return out

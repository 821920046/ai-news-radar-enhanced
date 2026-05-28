"""Pure utility functions for parsing, normalization, and session management."""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests
from dateutil import parser as dtparser
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import os
from scripts.models import BROWSER_UA, UTC


def _env_int(name: str, default: int, prefix: str = "") -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        prefix_str = f"[{prefix}] " if prefix else ""
        logger.warning("%sInvalid %s=%r; using %d.", prefix_str, name, raw, default)
        return default


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def iso(dt: datetime | None) -> str | None:
    if not dt:
        return None
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_iso(dt_str: str | None) -> datetime | None:
    if not dt_str:
        return None
    try:
        dt = dtparser.parse(dt_str)
    except Exception:
        return None
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def event_time(record: dict[str, Any]) -> datetime | None:
    """Derive the best timestamp for a record.
    RSS sources must rely on the source's publish time only
    (first_seen_at is fetch time and would falsely mark historical items as '24h').
    """
    if str(record.get("site_id") or "") == "opmlrss":
        return parse_iso(record.get("published_at"))
    return parse_iso(record.get("published_at")) or parse_iso(record.get("first_seen_at"))


def normalize_url(raw_url: str) -> str:
    try:
        parsed = urlparse(raw_url.strip())
        if not parsed.scheme:
            return raw_url.strip()
        query = []
        for k, v in parse_qsl(parsed.query, keep_blank_values=True):
            lk = k.lower()
            if lk.startswith("utm_"):
                continue
            if lk in {
                "ref",
                "spm",
                "fbclid",
                "gclid",
                "igshid",
                "mkt_tok",
                "mc_cid",
                "mc_eid",
                "_hsenc",
                "_hsmi",
            }:
                continue
            query.append((k, v))
        parsed = parsed._replace(
            scheme=parsed.scheme.lower(),
            netloc=parsed.netloc.lower(),
            fragment="",
            query=urlencode(query, doseq=True),
        )
        normalized = urlunparse(parsed)
        return normalized.rstrip("/")
    except Exception:
        return raw_url.strip()


def host_of_url(raw_url: str) -> str:
    try:
        return urlparse(raw_url).netloc.lower()
    except Exception:
        return ""


def first_non_empty(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        s = str(value).strip()
        if s:
            return s
    return ""


def maybe_fix_mojibake(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return s
    # Common mojibake signature from UTF-8 bytes decoded as Latin-1.
    if re.search(r"[Ãâåèæïð]|[\x80-\x9f]|æ|ç|å|é", s) is None:
        return s
    for enc in ("latin1", "cp1252"):
        try:
            fixed = s.encode(enc).decode("utf-8")
            if fixed and fixed != s:
                return fixed
        except Exception:
            continue
    return s


def has_cjk(text: str) -> bool:
    return bool(re.search(r"[一-鿿]", text or ""))


def is_mostly_english(text: str) -> bool:
    s = (text or "").strip()
    if not s:
        return False
    if has_cjk(s):
        return False
    letters = re.findall(r"[A-Za-z]", s)
    return len(letters) >= max(6, len(s) // 4)


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_IMAGE_TAG_RE = re.compile(r"<(?:meta|link|img)\b[^>]*>", re.I)
_HTML_ATTR_RE = re.compile(r"([a-zA-Z_:.-]+)\s*=\s*(['\"])(.*?)\2", re.S)
_META_IMAGE_KEYS = {"og:image", "og:image:url", "twitter:image", "twitter:image:src"}
_LINK_IMAGE_RELS = {"image_src"}
_PLACEHOLDER_IMAGE_RE = re.compile(
    r"(?:favicon|apple-touch-icon|sprite|spacer|placeholder|default-avatar|/avatar/|/logo(?:[-_.]|$))",
    re.I,
)
_MAX_DESC_LEN = 200
_MAX_IMAGE_HTML_BYTES = 2_000_000


def strip_html_tags(html: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    text = _HTML_TAG_RE.sub("", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def truncate_description(text: str, max_len: int = _MAX_DESC_LEN) -> str:
    """Truncate description to max_len characters."""
    text = strip_html_tags(text)
    if len(text) > max_len:
        return text[:max_len].rsplit(" ", 1)[0] + "..."
    return text


def normalize_image_url(raw_url: Any, base_url: str = "") -> str:
    """Normalize a candidate image URL and reject unsupported inline images."""
    url = str(raw_url or "").strip()
    if not url:
        return ""
    if "," in url and " " in url:
        url = url.split(",", 1)[0].strip().split(" ", 1)[0].strip()
    if url.startswith("//"):
        url = "https:" + url
    if base_url:
        url = urljoin(base_url, url)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return ""
    return urlunparse(parsed._replace(fragment="")).strip()


def _html_attrs(tag: str) -> dict[str, str]:
    return {m.group(1).lower(): html_lib.unescape(m.group(3).strip()) for m in _HTML_ATTR_RE.finditer(tag)}


def _srcset_candidates(srcset: str) -> list[str]:
    out: list[str] = []
    for part in str(srcset or "").split(","):
        candidate = part.strip().split(" ", 1)[0].strip()
        if candidate:
            out.append(candidate)
    return out


def _is_probable_placeholder_image(url: str) -> bool:
    if not url:
        return True
    return bool(_PLACEHOLDER_IMAGE_RE.search(url))


def _normalize_content_image(raw_url: Any, base_url: str = "") -> str:
    url = normalize_image_url(raw_url, base_url)
    if not url or _is_probable_placeholder_image(url):
        return ""
    return url


def extract_image_url_from_html(html: str, base_url: str = "") -> str:
    """Extract the first usable content image URL from an HTML snippet."""
    if not html:
        return ""
    candidates: list[Any] = []
    for tag in _HTML_IMAGE_TAG_RE.findall(html):
        attrs = _html_attrs(tag)
        tag_l = tag[:12].lower()
        if tag_l.startswith("<meta"):
            key = (attrs.get("property") or attrs.get("name") or "").lower()
            if key in _META_IMAGE_KEYS:
                candidates.append(attrs.get("content"))
        elif tag_l.startswith("<link"):
            rel = (attrs.get("rel") or "").lower()
            if rel in _LINK_IMAGE_RELS:
                candidates.append(attrs.get("href"))
        elif tag_l.startswith("<img"):
            candidates.extend(
                [
                    attrs.get("data-original"),
                    attrs.get("data-src"),
                    attrs.get("data-lazy-src"),
                    attrs.get("src"),
                ]
            )
            candidates.extend(_srcset_candidates(attrs.get("srcset") or ""))

    for candidate in candidates:
        url = _normalize_content_image(candidate, base_url)
        if url:
            return url
    return ""


def extract_image_url_from_feed_entry(entry: Any, base_url: str = "") -> str:
    """Extract image URLs from common feedparser entry structures."""
    candidates: list[Any] = []

    for key in ("image", "thumbnail", "itunes_image"):
        value = entry.get(key) if hasattr(entry, "get") else None
        if isinstance(value, dict):
            candidates.extend([value.get("href"), value.get("url")])
        else:
            candidates.append(value)

    for key in ("media_thumbnail", "media_content", "enclosures", "links"):
        values = entry.get(key) if hasattr(entry, "get") else None
        if isinstance(values, dict):
            values = [values]
        for value in values or []:
            if not isinstance(value, dict):
                continue
            mime = str(value.get("type") or value.get("medium") or "").lower()
            rel = str(value.get("rel") or "").lower()
            if mime and not (mime.startswith("image/") or mime == "image"):
                continue
            if rel and rel not in {"enclosure", "image", "thumbnail", "alternate"}:
                continue
            candidates.extend([value.get("url"), value.get("href")])

    raw_html = str(
        (entry.get("summary") if hasattr(entry, "get") else "")
        or (entry.get("description") if hasattr(entry, "get") else "")
        or ((entry.get("content", [{}])[0].get("value")) if hasattr(entry, "get") and entry.get("content") else "")
        or ""
    )
    candidates.append(extract_image_url_from_html(raw_html, base_url))

    for candidate in candidates:
        url = _normalize_content_image(candidate, base_url)
        if url:
            return url
    return ""


def fetch_article_image_url(session: requests.Session, page_url: str, timeout: int = 8) -> str:
    """Fetch an article page and extract its Open Graph/Twitter/content image."""
    url = str(page_url or "").strip()
    if not url.startswith(("http://", "https://")):
        return ""
    try:
        resp = session.get(
            url,
            timeout=timeout,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        resp.raise_for_status()
    except requests.RequestException:
        return ""

    content_type = str(resp.headers.get("content-type") or "").lower()
    if content_type and "html" not in content_type and "text/" not in content_type:
        return ""
    raw = resp.content[:_MAX_IMAGE_HTML_BYTES]
    encoding = resp.encoding or resp.apparent_encoding or "utf-8"
    html = raw.decode(encoding, errors="replace")
    return extract_image_url_from_html(html, resp.url or url)


def enrich_missing_article_images(
    items: list[dict[str, Any]],
    session: requests.Session,
    *,
    max_items: int = 80,
    max_workers: int = 8,
    timeout: int = 8,
) -> int:
    """Fill missing image_url fields by inspecting original article pages."""
    if max_items <= 0:
        return 0

    candidates: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for item in items:
        if item.get("image_url"):
            continue
        url = str(item.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        key = normalize_url(url)
        if key in seen_urls:
            continue
        seen_urls.add(key)
        candidates.append(item)
        if len(candidates) >= max_items:
            break

    if not candidates:
        return 0

    found_by_url: dict[str, str] = {}
    worker_count = min(max(1, max_workers), len(candidates))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_to_url = {
            executor.submit(fetch_article_image_url, session, str(item.get("url") or ""), timeout): normalize_url(
                str(item.get("url") or "")
            )
            for item in candidates
        }
        for future in as_completed(future_to_url):
            try:
                image_url = future.result()
            except Exception:
                continue
            if image_url:
                found_by_url[future_to_url[future]] = image_url

    if not found_by_url:
        return 0

    updated = 0
    for item in items:
        if item.get("image_url"):
            continue
        image_url = found_by_url.get(normalize_url(str(item.get("url") or "")))
        if image_url:
            item["image_url"] = image_url
            updated += 1
    return updated


def parse_feed_entries_via_xml(feed_xml: bytes) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    try:
        root = ET.fromstring(feed_xml)
    except Exception:
        return out

    for tag in (".//item", ".//{*}item", ".//entry", ".//{*}entry"):
        for node in root.findall(tag):
            title = (
                node.findtext("title")
                or node.findtext("{*}title")
                or ""
            ).strip()
            link = ""
            link_node = node.find("link")
            if link_node is None:
                link_node = node.find("{*}link")
            if link_node is not None:
                link = (link_node.get("href") or link_node.text or "").strip()
            if not link:
                link = (node.findtext("{*}link") or node.findtext("link") or "").strip()
            published = (
                node.findtext("pubDate")
                or node.findtext("{*}pubDate")
                or node.findtext("published")
                or node.findtext("{*}published")
                or node.findtext("updated")
                or node.findtext("{*}updated")
            )
            # Extract description from common RSS/Atom fields
            raw_desc = (
                node.findtext("description")
                or node.findtext("{*}description")
                or node.findtext("summary")
                or node.findtext("{*}summary")
                or node.findtext("{*}content")
                or node.findtext("content")
                or ""
            )
            description = truncate_description(raw_desc) if raw_desc else ""
            image_url = ""
            media_node = node.find("{*}thumbnail")
            if media_node is None:
                media_node = node.find("{*}content")
            if media_node is not None:
                medium = str(media_node.get("medium") or media_node.get("type") or "").lower()
                if not medium or medium == "image" or medium.startswith("image/"):
                    image_url = normalize_image_url(media_node.get("url"), link)
            if not image_url:
                enclosure = node.find("enclosure")
                if enclosure is None:
                    enclosure = node.find("{*}enclosure")
                if enclosure is not None and str(enclosure.get("type") or "").lower().startswith("image/"):
                    image_url = normalize_image_url(enclosure.get("url"), link)
            if not image_url:
                image_url = extract_image_url_from_html(raw_desc, link)

            if title and link:
                key = (title, link)
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    {
                        "title": title,
                        "link": link,
                        "published": published,
                        "description": description,
                        "image_url": image_url,
                    }
                )
    return out


def make_item_id(site_id: str, source: str, title: str, url: str) -> str:
    key = "||".join(
        [
            site_id.strip().lower(),
            source.strip().lower(),
            title.strip().lower(),
            normalize_url(url),
        ]
    )
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def parse_unix_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        n = float(value)
    except Exception:
        return None
    if n > 10_000_000_000:
        n /= 1000.0
    try:
        return datetime.fromtimestamp(n, tz=UTC)
    except Exception:
        return None


def parse_relative_time_zh(text: str, now: datetime) -> datetime | None:
    text = (text or "").strip()
    if not text:
        return None

    m = re.search(r"(\d+)\s*分钟前", text)
    if m:
        return now - timedelta(minutes=int(m.group(1)))

    m = re.search(r"(\d+)\s*小时前", text)
    if m:
        return now - timedelta(hours=int(m.group(1)))

    m = re.search(r"(\d+)\s*天前", text)
    if m:
        return now - timedelta(days=int(m.group(1)))

    if "刚刚" in text:
        return now

    if "昨天" in text:
        return now - timedelta(days=1)

    m = re.fullmatch(r"(?:今天)?\s*(\d{1,2}):(\d{2})", text)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate > now + timedelta(minutes=5):
            candidate -= timedelta(days=1)
        return candidate

    m = re.fullmatch(r"昨天\s*(\d{1,2}):(\d{2})", text)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        return (now - timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)

    m = re.fullmatch(r"(?:\d{4}年\s*)?(\d{1,2})月(\d{1,2})日", text)
    if m:
        month = int(m.group(1))
        day = int(m.group(2))
        year = now.year
        try:
            candidate = datetime(year, month, day, tzinfo=UTC)
            if candidate > now + timedelta(days=2):
                candidate = datetime(year - 1, month, day, tzinfo=UTC)
            return candidate
        except Exception:
            return None

    return None


def parse_date_any(value: Any, now: datetime) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        return value.astimezone(UTC)

    if isinstance(value, (int, float)):
        return parse_unix_timestamp(value)

    s = str(value).strip()
    if not s:
        return None

    if s.startswith("$D"):
        s = s[2:]

    if re.fullmatch(r"\d{12,}", s):
        return parse_unix_timestamp(int(s))

    if re.fullmatch(r"\d{9,11}", s):
        return parse_unix_timestamp(int(s))

    dt = parse_relative_time_zh(s, now)
    if dt:
        return dt

    # TechURLs format: 2026-02-19 11:54:21AM UTC
    m = re.search(r"(\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}:\d{2}[AP]M)\s+UTC", s)
    if m:
        try:
            dt = datetime.strptime(m.group(1), "%Y-%m-%d %I:%M:%S%p")
            return dt.replace(tzinfo=UTC)
        except Exception:
            pass

    try:
        dt = dtparser.parse(s, tzinfos={"UT": 0, "UTC": 0, "GMT": 0})
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def decode_escaped_json(raw: str) -> dict[str, Any] | None:
    s = raw.replace('\\"', '"').replace("\\/", "/")
    try:
        return json.loads(s)
    except Exception:
        return None


def create_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.8,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "POST"]),
    )
    adapter = HTTPAdapter(pool_connections=35, pool_maxsize=35, max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": BROWSER_UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    return session


# ---------------------------------------------------------------------------
# Hotness scoring
# ---------------------------------------------------------------------------

def _parse_metric_number(raw: str) -> float:
    """Parse a human-readable metric like '1.2万', '3,456', '12k' into a number."""
    s = (raw or "").strip().replace(",", "").replace(" ", "")
    if not s:
        return 0
    multiplier = 1
    if s.endswith("万"):
        s = s[:-1]
        multiplier = 10_000
    elif s.endswith("k") or s.endswith("K"):
        s = s[:-1]
        multiplier = 1_000
    try:
        return float(s) * multiplier
    except ValueError:
        return 0


def compute_hotness(record: dict[str, Any]) -> tuple[float, str]:
    """Return (score, raw_display) for an archived/latest record.

    Score is 0-1000 normalized.  Higher = hotter.
    raw_display is a human-readable string like "1.2万" or "" if no metric.
    """
    site_id = str(record.get("site_id") or "")
    meta = record.get("meta") or {}
    if not isinstance(meta, dict):
        meta = {}

    # TopHub: has explicit view/like count in meta.metric
    if site_id == "tophub":
        raw = str(meta.get("metric") or "")
        n = _parse_metric_number(raw)
        if n > 0:
            # TopHub numbers range from ~100 to ~10M; map to 0-1000 on log scale
            import math
            score = min(1000, max(0, math.log10(max(1, n)) * 200))
            return (score, raw)

    # Zeli (HN): position-based (earlier in list = hotter)
    if site_id == "zeli":
        hn_id = meta.get("hn_id")
        if hn_id:
            # Items from Zeli are already sorted by hotness; use a default high score
            return (600, "HN热门")

    # Buzzing: items are in hotness order
    if site_id == "buzzing":
        return (500, "热榜")

    # NewsNow: aggregated from multiple hot sources
    if site_id == "newsnow":
        return (400, "聚合热榜")

    # Official sources: high signal by default
    if site_id == "official_ai":
        return (350, "")

    # AI Breakfast: curated newsletter
    if site_id == "aibreakfast":
        return (300, "")

    # Follow Builders: curated builder feed
    if site_id == "followbuilders":
        return (250, "")

    # Everything else
    return (0, "")


def add_hotness_scores(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add hotness_score and hotness_raw fields to each item in-place."""
    for item in items:
        score, raw = compute_hotness(item)
        item["hotness_score"] = round(score)
        item["hotness_raw"] = raw
    return items

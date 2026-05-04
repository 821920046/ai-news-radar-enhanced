"""Pure utility functions for parsing, normalization, and session management."""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from dateutil import parser as dtparser
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from scripts.models import BROWSER_UA, UTC


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
            if title and link:
                key = (title, link)
                if key in seen:
                    continue
                seen.add(key)
                out.append({"title": title, "link": link, "published": published})
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
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": BROWSER_UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    return session

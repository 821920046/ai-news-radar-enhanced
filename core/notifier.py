"""Optional webhook notifications for AI News Radar summaries."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from core.utils import _env_int
from core.normalize.normalizer import (
    NOISE_KEYWORDS,
    TOPIC_TECH_KEYWORDS,
    contains_any_keyword,
    contains_meaningful_ai_signal,
)

logger = logging.getLogger(__name__)

DEFAULT_HOTNESS_THRESHOLD = 150
DEFAULT_DIGEST_LIMIT = 5
DEFAULT_BREAKING_LIMIT = 10





def _compact(text: Any, limit: int = 180) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)].rstrip() + "..."


def filter_breaking_news(items: list[dict[str, Any]], hotness_threshold: int = DEFAULT_HOTNESS_THRESHOLD) -> list[dict[str, Any]]:
    """Pick high-hotness or explicitly important items for alert-style pushes."""
    breaking: list[dict[str, Any]] = []
    seen: set[str] = set()
    important_tags = {"重磅", "首发", "模型发布", "安全对齐"}

    for item in items:
        title = str(item.get("title_zh") or item.get("title") or "").strip()
        if not title:
            continue
        dedupe_key = title.lower()
        if dedupe_key in seen:
            continue

        score = int(item.get("hotness_score") or 0)
        tags = {str(tag).strip() for tag in item.get("tags") or []}
        if score >= hotness_threshold or tags.intersection(important_tags):
            breaking.append(item)
            seen.add(dedupe_key)

    breaking.sort(key=lambda item: int(item.get("hotness_score") or 0), reverse=True)
    return breaking


def select_digest_items(items: list[dict[str, Any]], limit: int = DEFAULT_DIGEST_LIMIT) -> list[dict[str, Any]]:
    """Pick a stable Top N digest, preferring hotness then newest items."""
    ranked = list(items)
    ranked.sort(
        key=lambda item: (
            int(item.get("hotness_score") or 0),
            str(item.get("published_at") or item.get("first_seen_at") or ""),
        ),
        reverse=True,
    )
    return ranked[: max(0, limit)]


def build_markdown_message(items: list[dict[str, Any]], *, title: str = "AI News Radar") -> str:
    lines = [f"**{title}**"]
    for idx, item in enumerate(items, 1):
        headline = _compact(item.get("title_zh") or item.get("title"), 100)
        source = _compact(item.get("site_name") or item.get("source") or "AI News Radar", 40)
        score = int(item.get("hotness_score") or 0)
        tldr = _compact(item.get("tldr") or item.get("description") or "", 140)
        url = item.get("url") or "#"

        lines.append("")
        lines.append(f"{idx}. [{headline}]({url})")
        lines.append(f"Source: {source} | Hotness: {score}")
        if tldr:
            lines.append(f"TL;DR: {tldr}")
    return "\n".join(lines)


def build_webhook_payload(markdown: str, webhook_type: str) -> dict[str, Any]:
    webhook_type = (webhook_type or "markdown").strip().lower()
    if webhook_type in {"feishu", "lark"}:
        return {"msg_type": "text", "content": {"text": markdown}}
    if webhook_type in {"wechat", "wecom", "dingtalk", "dingding", "markdown"}:
        return {"msgtype": "markdown", "markdown": {"content": markdown}}
    return {"text": markdown}


def send_webhook_notification(items: list[dict[str, Any]], *, title: str = "AI News Radar") -> bool:
    """Send a digest or breaking-news list if WEBHOOK_URL is configured."""
    if not items:
        logger.info("[IM Notifier] No items selected for notification.")
        return False

    webhook_url = os.environ.get("WEBHOOK_URL", "").strip()
    if not webhook_url:
        logger.info("[IM Notifier] WEBHOOK_URL is not set; skipping notification.")
        return False

    webhook_type = os.environ.get("WEBHOOK_TYPE", "markdown")
    markdown = build_markdown_message(items, title=title)
    payload = build_webhook_payload(markdown, webhook_type)

    try:
        response = requests.post(webhook_url, json=payload, timeout=10)
    except requests.exceptions.RequestException as exc:
        logger.error("[IM Notifier] Webhook request failed: %s", exc)
        return False

    if 200 <= response.status_code < 300:
        logger.info("[IM Notifier] Delivered %d items via webhook.", len(items))
        return True

    logger.error("[IM Notifier] Delivery failed: %s %s", response.status_code, response.text[:200])
    return False


# ---- Daily categorized digest (fixed 07:00 Beijing time / UTC+8) ------------

DEFAULT_DAILY_HOUR_CST = 7
DEFAULT_DAILY_PER_GROUP = 3
# 每个推送类目 -> 归入该类目的 category 取值集合
# 「AI」大类目已并入原「科技」；「3C数码」涵盖 数码 / 手机 / 电脑硬件
DAILY_DIGEST_GROUPS: list[tuple[str, tuple[str, ...]]] = [
    ("AI", ("AI", "科技")),
    ("3C数码", ("数码", "手机", "电脑硬件")),
]

# 推送选材相关性闸门：视为"具体品类"的 category
_DIGEST_CONCRETE_CATEGORIES = {"手机", "电脑硬件", "数码", "开源热榜", "AI"}


def _is_digest_worthy(item: dict[str, Any]) -> bool:
    """推送选材相关性闸门：滤掉明显跑题/娱乐八卦类噪声，只推真正的科技/AI 情报。
    根因：HN 24h 热榜等来源会绕过 AI 相关性过滤，导致枪击、政治等硬新闻混入。
    注：相关性只看【内容】（标题+摘要），不看来源名——因为 TOPIC_TECH_KEYWORDS 含
    "hacker news""36氪" 等来源名关键词，若纳入 source 会让任意 HN 条目都误判为相关。"""
    text = " ".join(
        str(item.get(k) or "") for k in ("title_zh", "title", "description")
    ).lower()
    if contains_any_keyword(text, NOISE_KEYWORDS):
        return False
    if str(item.get("category") or "") in _DIGEST_CONCRETE_CATEGORIES:
        return True
    if contains_meaningful_ai_signal(text):
        return True
    if contains_any_keyword(text, TOPIC_TECH_KEYWORDS):
        return True
    return False


def _clip(text: Any, limit: int, *, ellipsis: bool = True) -> str:
    """折叠空白并截断到指定字数（最终长度不超过 limit）。"""
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    if ellipsis and limit >= 1:
        return value[: limit - 1].rstrip() + "…"
    return value[:limit].rstrip()


def _beijing_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8)))


def select_daily_digest(
    items: list[dict[str, Any]], per_group: int = DEFAULT_DAILY_PER_GROUP
) -> list[tuple[str, list[dict[str, Any]]]]:
    """按类目分组，每组取热度最高的 per_group 条（标题去重）。"""
    groups: list[tuple[str, list[dict[str, Any]]]] = []
    for label, cats in DAILY_DIGEST_GROUPS:
        cat_set = set(cats)
        seen: set[str] = set()
        pool: list[dict[str, Any]] = []
        for item in items:
            if str(item.get("category") or "") not in cat_set:
                continue
            if not _is_digest_worthy(item):
                continue
            title = str(item.get("title_zh") or item.get("title") or "").strip()
            if not title:
                continue
            key = title.lower()
            if key in seen:
                continue
            seen.add(key)
            pool.append(item)
        pool.sort(
            key=lambda it: (
                int(it.get("hotness_score") or 0),
                int(it.get("signal_score") or 0),
                str(it.get("published_at") or it.get("first_seen_at") or ""),
            ),
            reverse=True,
        )
        groups.append((label, pool[: max(0, per_group)]))
    return groups


# 推送类目 -> 展示用 emoji
DAILY_GROUP_EMOJI = {"AI": "🤖", "3C数码": "📱"}

_HEADLINE_MAX = 34
_SUMMARY_MAX = 54
_MESSAGE_BYTE_BUDGET = 3800


def _pick_summary(item: dict[str, Any], full_title: str) -> str:
    """挑选一句真正有信息量的摘要：优先 tldr，其次 description；
    与标题重复或为空则视为无摘要，避免"正文只是复读标题"。"""
    for key in ("tldr", "description"):
        text = re.sub(r"\s+", " ", str(item.get(key) or "")).strip()
        if text and text != full_title:
            return _clip(text, _SUMMARY_MAX)
    return ""


def _digest_meta_line(item: dict[str, Any]) -> str:
    """来源 · 标签 · 信号/热榜 的上下文信息行（替代无意义的标题复读）。"""
    parts: list[str] = []
    # 清洗来源：去掉板块后缀（"Hacker News · 24h最热" -> "Hacker News"）与冗余括号注释
    raw_source = str(item.get("source") or item.get("site_name") or "").strip()
    raw_source = re.split(r"\s*[·|]\s*", raw_source)[0]
    raw_source = re.sub(r"\s*[（(].*?[)）]\s*$", "", raw_source).strip()
    source = _clip(raw_source, 20)
    if source:
        parts.append(source)
    tags = [str(t).strip() for t in (item.get("tags") or []) if str(t).strip()]
    if tags:
        parts.append(" ".join(f"#{t}" for t in tags[:2]))
    try:
        score = int(round(float(item.get("signal_score") or 0)))
    except (TypeError, ValueError):
        score = 0
    if score:
        parts.append(f"信号 {score}{str(item.get('signal_level') or '').strip()}")
    if str(item.get("hotness_raw") or "").strip():
        parts.append("🔥 热榜")
    return " · ".join(parts)


def build_daily_digest_message(
    groups: list[tuple[str, list[dict[str, Any]]]], *, title: str = "每日科技情报"
) -> str:
    """构造企业微信/钉钉 markdown 每日精选：
    - 标题不再硬截断到 10 字（改为完整中文标题，过长��省略）；
    - 正文优先展示真实摘要（tldr/description），无摘要时展示来源/标签/信号上下文，
      不再复读标题；
    - 分类分节 + emoji + 日期表头，排版更清晰易扫读。"""
    total = sum(len(rows) for _, rows in groups)
    now_bj = _beijing_now()
    weekday = "一二三四五六日"[now_bj.weekday()]
    group_names = " / ".join(label for label, _rows in groups) or "科技情报"
    lines = [
        f"# 📡 {title} · {now_bj.month}月{now_bj.day}日 周{weekday}",
        f"> 每日 {total} 条精选 · {group_names}",
    ]
    idx = 0
    for label, rows in groups:
        emoji = DAILY_GROUP_EMOJI.get(label, "•")
        lines.append("")
        lines.append(f"**{emoji} {label}**")
        if not rows:
            lines.append("> 今日暂无热点")
            continue
        for item in rows:
            idx += 1
            full_title = re.sub(
                r"\s+", " ", str(item.get("title_zh") or item.get("title") or "")
            ).strip()
            headline = _clip(full_title, _HEADLINE_MAX) or "未命名"
            url = item.get("url") or "#"
            summary = _pick_summary(item, full_title)
            meta = _digest_meta_line(item)
            lines.append(f"**{idx}.** [{headline}]({url})")
            if summary:
                lines.append(f"> {summary}")
            if meta:
                lines.append(f'<font color="comment">{meta}</font>')
    markdown = "\n".join(lines)
    # 兜底：企业微信单条 markdown content 上限约 4096 字节，超限则安全截断
    if len(markdown.encode("utf-8")) > _MESSAGE_BYTE_BUDGET:
        encoded = markdown.encode("utf-8")[:_MESSAGE_BYTE_BUDGET]
        markdown = encoded.decode("utf-8", "ignore").rstrip() + "\n> …"
    return markdown


def _post_markdown(markdown: str) -> bool:
    """底层推送：根据 WEBHOOK_TYPE 构造 payload 并 POST 到 WEBHOOK_URL。"""
    webhook_url = os.environ.get("WEBHOOK_URL", "").strip()
    if not webhook_url:
        logger.info("[IM Notifier] WEBHOOK_URL is not set; skipping notification.")
        return False
    webhook_type = os.environ.get("WEBHOOK_TYPE", "markdown")
    payload = build_webhook_payload(markdown, webhook_type)
    try:
        response = requests.post(webhook_url, json=payload, timeout=10)
    except requests.exceptions.RequestException as exc:
        logger.error("[IM Notifier] Webhook request failed: %s", exc)
        return False
    if 200 <= response.status_code < 300:
        logger.info("[IM Notifier] Delivered notification via webhook.")
        return True
    logger.error("[IM Notifier] Delivery failed: %s %s", response.status_code, response.text[:200])
    return False


def send_daily_digest(items: list[dict[str, Any]]) -> bool:
    """发送固定每日推送（6 条：AI / 数码科技 / 手机电脑，每组 2 条）。

    仅在北京时间命中目标小时（默认 7 点）时才真正发送；可用 WEBHOOK_FORCE 强制发送。
    """
    force = os.environ.get("WEBHOOK_FORCE", "").strip().lower() in {"1", "true", "yes", "on"}
    target_hour = _env_int("WEBHOOK_DAILY_HOUR", DEFAULT_DAILY_HOUR_CST, prefix="IM Notifier")
    now_bj = _beijing_now()
    if not force and now_bj.hour != target_hour:
        logger.info(
            "[IM Notifier] Daily digest gated: Beijing hour %d != target %d; skipping.",
            now_bj.hour,
            target_hour,
        )
        return False

    per_group = _env_int("WEBHOOK_DAILY_PER_GROUP", DEFAULT_DAILY_PER_GROUP, prefix="IM Notifier")
    groups = select_daily_digest(items, per_group)
    if not any(rows for _, rows in groups):
        logger.info("[IM Notifier] Daily digest has no items; skipping.")
        return False

    markdown = build_daily_digest_message(groups, title="每日科技情报")
    return _post_markdown(markdown)


def maybe_send_news_notification(items: list[dict[str, Any]]) -> bool:
    """Route notification mode from env while keeping the pipeline optional.

    默认模式为 "daily"：每天北京时间 07:00 固定推送 6 条分类精选
    （AI / 数码科技 / 手机电脑，每组 2 条）。
    旧的 "breaking"（热点即时推送）与 "digest" 模式仍可通过 WEBHOOK_MODE 使用。
    """
    mode = os.environ.get("WEBHOOK_MODE", "daily").strip().lower()
    if mode in {"0", "false", "no", "off", "none"}:
        logger.info("[IM Notifier] WEBHOOK_MODE disables notifications.")
        return False

    if mode == "breaking":
        threshold = _env_int("WEBHOOK_HOTNESS_THRESHOLD", DEFAULT_HOTNESS_THRESHOLD, prefix="IM Notifier")
        limit = _env_int("WEBHOOK_BREAKING_LIMIT", DEFAULT_BREAKING_LIMIT, prefix="IM Notifier")
        selected = filter_breaking_news(items, threshold)[: max(0, limit)]
        return send_webhook_notification(selected, title="AI News Radar Breaking Alerts")

    if mode == "digest":
        limit = _env_int("WEBHOOK_DIGEST_LIMIT", DEFAULT_DIGEST_LIMIT, prefix="IM Notifier")
        selected = select_digest_items(items, limit)
        return send_webhook_notification(selected, title="AI News Radar Daily Digest")

    # 默认：固定每日 07:00（北京时间）分类推送
    return send_daily_digest(items)

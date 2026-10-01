#!/usr/bin/env python3
"""生成对外出口：llms.txt、分版 RSS、robots.txt。

为什么需要
----------
项目已有 173 个信源、每小时产出的高质量结构化数据，但此前只输出
一份 JSON 和一个网页。数据在那儿却没「开口」，机器消费者（大模型、
RSS 阅读器、聚合站）拿不到。

本脚本产出三个**纯静态**出口，由 GitHub Pages 直接托管，零成本零运维：

1. ``llms.txt``  —— 给大模型的站点说明（llmstxt.org 标准）。
   Agent 读到它就知道这个站有什么、怎么取、字段什么含义。
2. ``feed-*.xml`` —— 三版 RSS：精选(hot) / 全部(all) / 日报(daily)。
   一版混装会让订阅者被噪声淹没，分版是基本礼貌。
3. ``robots.txt`` —— 显式欢迎 AI 爬虫，并指向 sitemap 与 llms.txt。

设计约束
--------
* **不引入新依赖**：只用标准库，与项目的零成本定位一致。
* **不修改已有产物**：sitemap 仍由 prerender.py 生成，本脚本只做增量出口。
* **幂等**：每次都覆盖写，不追加，可被每小时的 CI 重复调用。
* **XML 转义必须完整**：RSS 里出现裸 ``&`` 会让整个 feed 解析失败，
  这是最常见的低级错误，所以统一走 ``_xml()``。

用法::

    python scripts/build_feeds.py --data data/latest-24h.json \
        --base-url https://821920046.github.io/ai-news-radar-enhanced \
        --out-dir .
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as _xml_escape

logger = logging.getLogger("build_feeds")

SITE_NAME = "AI Signal Board"
SITE_DESC = "24 小时 AI/科技情报雷达 — 多源聚合、智能评分、趋势检测。"
#: RSS 里每版最多放多少条（超过这个数订阅端会卡）
MAX_FEED_ITEMS = 50


# ── 转义与时间 ──────────────────────────────────────────────────────────


def _xml(text: Any) -> str:
    """XML 文本转义。

    ``&`` ``<`` ``>`` 由 saxutils 处理；引号单独补，因为属性值里也会用到。
    """
    if text is None:
        return ""
    return _xml_escape(str(text), {'"': "&quot;", "'": "&apos;"})


def _parse_dt(value: Any) -> datetime | None:
    """尽量解析一个时间戳；失败返回 None（不猜、不兜底成 now）。"""
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip()
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            # 退一步试 RFC 822（RSS 自己的格式）
            try:
                from email.utils import parsedate_to_datetime

                dt = parsedate_to_datetime(raw)
            except Exception:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _rfc822(dt: datetime) -> str:
    """RSS 要求 RFC 822 时间格式。"""
    return format_datetime(dt.astimezone(timezone.utc))


def _item_time(item: dict) -> datetime | None:
    """取内容的事件时间。优先 published_at，退回 first_seen_at。"""
    return _parse_dt(item.get("published_at")) or _parse_dt(item.get("first_seen_at"))


def _item_title(item: dict) -> str:
    return str(item.get("title_zh") or item.get("title") or item.get("title_en") or "无标题")


def _item_summary(item: dict) -> str:
    """拼一段可读的摘要：推荐理由 + 描述。"""
    parts: list[str] = []
    reason = item.get("recommendation_reason")
    if reason:
        parts.append(str(reason).strip())
    desc = item.get("description") or item.get("tldr")
    if desc:
        text = str(desc).strip()
        if text not in parts:
            parts.append(text)
    signal = item.get("signal_level")
    score = item.get("signal_score")
    if signal and score is not None:
        parts.append(f"信号等级 {signal} · 评分 {score}")
    return "\n\n".join(p for p in parts if p)


# ── RSS ────────────────────────────────────────────────────────────────


def build_rss(
    *,
    title: str,
    description: str,
    self_url: str,
    site_url: str,
    items: list[dict],
    generated_at: datetime,
) -> str:
    """构造一份 RSS 2.0 文档。"""
    lines: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        "  <channel>",
        f"    <title>{_xml(title)}</title>",
        f"    <link>{_xml(site_url)}</link>",
        f"    <description>{_xml(description)}</description>",
        "    <language>zh-CN</language>",
        f"    <lastBuildDate>{_rfc822(generated_at)}</lastBuildDate>",
        f'    <atom:link href="{_xml(self_url)}" rel="self" type="application/rss+xml"/>',
    ]

    for item in items[:MAX_FEED_ITEMS]:
        link = str(item.get("url") or "").strip()
        if not link:
            continue
        dt = _item_time(item)
        lines.append("    <item>")
        lines.append(f"      <title>{_xml(_item_title(item))}</title>")
        lines.append(f"      <link>{_xml(link)}</link>")
        # guid 用 url，isPermaLink=true —— 重复抓取不会产生新条目
        lines.append(f'      <guid isPermaLink="true">{_xml(link)}</guid>')
        if dt:
            lines.append(f"      <pubDate>{_rfc822(dt)}</pubDate>")
        summary = _item_summary(item)
        if summary:
            lines.append(f"      <description>{_xml(summary)}</description>")
        source = item.get("site_name") or item.get("source")
        if source:
            lines.append(f"      <category>{_xml(source)}</category>")
        for tag in (item.get("tags") or [])[:6]:
            lines.append(f"      <category>{_xml(tag)}</category>")
        lines.append("    </item>")

    lines += ["  </channel>", "</rss>", ""]
    return "\n".join(lines)


# ── llms.txt ───────────────────────────────────────────────────────────


def build_llms_txt(
    *,
    base_url: str,
    payload: dict,
    generated_at: datetime,
    hot_events: list[dict],
) -> str:
    """构造 llms.txt（llmstxt.org 约定）。

    Agent 读完这一页就应该知道：这个站是什么、有哪些出口、
    字段什么含义、怎么引用。宁可写清楚，也不要让 Agent 去猜。
    """
    total = payload.get("total_items") or len(payload.get("items_ai") or [])
    archive_total = payload.get("archive_total") or 0
    site_count = payload.get("site_count") or 0
    source_count = payload.get("source_count") or 0
    window = payload.get("window_hours") or 24

    lines: list[str] = [
        f"# {SITE_NAME}",
        "",
        f"> {SITE_DESC}",
        "",
        f"本站每小时自动更新，聚合 {site_count} 个信源 / {source_count} 个来源，"
        f"默认输出最近 {window} 小时的 AI/科技资讯，"
        f"每条内容经过多维信号评分（S/A/B/C 分级），并按事件计算跨来源热度。",
        "",
        f"最后更新：{generated_at.isoformat()}",
        f"当前窗口内容数：{total} 条；累计去重归档：{archive_total} 条。",
        "",
        "## 数据出口",
        "",
        f"- [首页]({base_url}/)：人类可读的看板（含首屏预渲染）",
        f"- [精选 RSS]({base_url}/feed-hot.xml)：信号评分较高的一批，适合订阅",
        f"- [全部 RSS]({base_url}/feed-all.xml)：窗口内全部内容，不筛不弃",
        f"- [日报 RSS]({base_url}/feed-daily.xml)：每天一期的摘要合集",
        f"- [24 小时快照 JSON]({base_url}/data/latest-24h.json)：完整结构化数据",
        f"- [全量快照 JSON]({base_url}/data/latest-24h-all.json)：未做主题过滤的全量数据",
        f"- [站点地图]({base_url}/sitemap.xml)",
        "",
        "## 数据契约",
        "",
        "`data/latest-24h.json` 的顶层字段：",
        "",
        "| 字段 | 含义 |",
        "| --- | --- |",
        "| `generated_at` | 生成时刻（UTC ISO 8601）。**判断数据新旧只看这个**，不要用 HTTP 头或文件时间 |",
        "| `window_hours` | 时间窗口小时数 |",
        "| `total_items` | 本次窗口内条目数 |",
        "| `archive_total` | 累计去重归档条目数 |",
        "| `items_ai` | 条目数组（已按主题过滤） |",
        "| `hot_events` | 事件级热度榜（见下） |",
        "| `heat_rule` | 热度算法版本标识 |",
        "| `heat_behind_sources` | 本轮缺席的信源，非空时热度方向不可信 |",
        "",
        "单条内容的字段：",
        "",
        "| 字段 | 含义 |",
        "| --- | --- |",
        "| `title` / `title_zh` | 原始标题 / 中文标题 |",
        "| `url` | 原文链接（引用时请用这个） |",
        "| `published_at` | 原文发布时间。可能为 `null`，此时退回 `first_seen_at` |",
        "| `first_seen_at` | 本站首次抓到的时刻（**不等于**发布时间） |",
        "| `site_name` / `source` | 信源站点 / 具体来源 |",
        "| `signal_score` | 多维信号分（0–100） |",
        "| `signal_level` | 分级 S/A/B/C |",
        "| `signal_breakdown` | 五维细分：信源权重、技术深度、新颖度、传播速度、社区信号 |",
        "| `hotness_score` | 该内容在**其来源站内**的排位热度（0–1000），不是跨站热度 |",
        "| `tags` | 主题标签 |",
        "| `recommendation_reason` | 推荐理由 |",
        "",
        "## 事件级热度",
        "",
        "`hot_events` 与单条的 `hotness_score` 含义不同：前者按**事件**统计"
        "有多少个**独立来源**在讨论，后者只是单条内容在其来源站内的排位。",
        "",
        "热度规则（`heat_rule`）：48 小时窗口、24 小时半衰期、"
        "同一来源无论发几条只计一次。",
        "",
        "`heat_trend` 取值：`up` / `down` / `flat` / `new` / `unknown`。",
        "**`unknown` 表示有信源本轮未产出数据，方向无法判定 —— 不要把它当作下降。**",
        "",
    ]

    if hot_events:
        lines += ["当前热度榜（前 10）：", ""]
        for idx, ev in enumerate(hot_events[:10], 1):
            topic = ev.get("topic") or "未命名话题"
            heat = ev.get("heat")
            participants = ev.get("heat_participants")
            trend = ev.get("heat_trend")
            lines.append(
                f"{idx}. {topic} — 热度 {heat}，{participants} 个独立来源，趋势 {trend}"
            )
        lines.append("")

    lines += [
        "## 引用要求",
        "",
        "- 引用内容时请链接到 `url` 字段指向的**原文**，本站是聚合器，不是原始发布方。",
        "- 引用数据时请注明本站名称与抓取时刻（`generated_at`）。",
        "- 请勿把 `first_seen_at` 当作发布时间引用。",
        "",
    ]
    return "\n".join(lines)


# ── robots.txt ─────────────────────────────────────────────────────────


def build_robots(base_url: str) -> str:
    """显式欢迎 AI 爬虫，并指向 sitemap 与 llms.txt。"""
    return "\n".join(
        [
            "User-agent: *",
            "Allow: /",
            "",
            "# 本站是公开聚合数据，欢迎 AI 爬虫抓取。",
            "User-agent: GPTBot",
            "Allow: /",
            "",
            "User-agent: ClaudeBot",
            "Allow: /",
            "",
            "User-agent: Google-Extended",
            "Allow: /",
            "",
            "User-agent: PerplexityBot",
            "Allow: /",
            "",
            f"Sitemap: {base_url}/sitemap.xml",
            f"# LLM 说明: {base_url}/llms.txt",
            "",
        ]
    )


# ── 入口 ───────────────────────────────────────────────────────────────


def _select_hot_items(items: list[dict], limit: int) -> list[dict]:
    """挑出「精选」：S/A 级优先，其次按 signal_score 降序。"""
    tier = {"S": 0, "A": 1, "B": 2, "C": 3, "": 4}

    def key(it: dict) -> tuple:
        lvl = str(it.get("signal_level") or "")
        return (tier.get(lvl, 4), -float(it.get("signal_score") or 0))

    return sorted(items, key=key)[:limit]


def _select_daily_items(items: list[dict], generated_at: datetime) -> list[dict]:
    """日报只取最近 24 小时内、且信号等级不低于 B 的内容。"""
    cutoff = generated_at - timedelta(hours=24)
    out: list[dict] = []
    for it in items:
        dt = _item_time(it)
        if dt is None or dt < cutoff:
            continue
        if str(it.get("signal_level") or "C") in {"S", "A", "B"}:
            out.append(it)
    return out


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="生成 llms.txt / 分版 RSS / robots.txt")
    ap.add_argument("--data", default="data/latest-24h.json")
    ap.add_argument("--base-url", default="", help="站点绝对 URL（留空则跳过需要绝对链接的产物）")
    ap.add_argument("--out-dir", default=".", help="产物输出目录")
    ap.add_argument("--skip-robots", action="store_true", help="不生成 robots.txt")
    args = ap.parse_args()

    base_url = (args.base_url or "").rstrip("/")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    payload: dict = {}
    data_path = Path(args.data)
    if data_path.exists():
        try:
            payload = json.loads(data_path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("数据文件解析失败，将使用空数据: %s", data_path)
    else:
        logger.warning("数据文件不存在: %s", data_path)

    items = payload.get("items_ai") or payload.get("items") or []
    hot_events = payload.get("hot_events") or []
    generated_at = _parse_dt(payload.get("generated_at")) or datetime.now(timezone.utc)

    written: list[str] = []

    # ── llms.txt ──
    if base_url:
        llms = build_llms_txt(
            base_url=base_url,
            payload=payload,
            generated_at=generated_at,
            hot_events=hot_events,
        )
        p = out_dir / "llms.txt"
        p.write_text(llms, encoding="utf-8")
        written.append(str(p))

    # ── 三版 RSS ──
    if base_url:
        feeds = [
            (
                "feed-hot.xml",
                f"{SITE_NAME} — 精选",
                "信号评分较高的一批 AI/科技情报。",
                _select_hot_items(items, MAX_FEED_ITEMS),
            ),
            (
                "feed-all.xml",
                f"{SITE_NAME} — 全部动态",
                f"最近 {payload.get('window_hours') or 24} 小时全部内容，不筛不弃。",
                items,
            ),
            (
                "feed-daily.xml",
                f"{SITE_NAME} — 日报",
                "每天一期：近 24 小时内信号等级 B 及以上的内容。",
                _select_daily_items(items, generated_at),
            ),
        ]
        for filename, title, desc, subset in feeds:
            xml = build_rss(
                title=title,
                description=desc,
                self_url=f"{base_url}/{filename}",
                site_url=base_url,
                items=subset,
                generated_at=generated_at,
            )
            p = out_dir / filename
            p.write_text(xml, encoding="utf-8")
            written.append(str(p))
            logger.info("RSS 生成: %s（%d 条）", p, min(len(subset), MAX_FEED_ITEMS))

    # ── robots.txt ──
    if base_url and not args.skip_robots:
        p = out_dir / "robots.txt"
        p.write_text(build_robots(base_url), encoding="utf-8")
        written.append(str(p))

    if not written:
        logger.warning("未生成任何产物（base-url 为空？）")
        return 0

    logger.info("对外出口生成完成，共 %d 个文件: %s", len(written), ", ".join(written))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

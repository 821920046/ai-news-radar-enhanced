"""Editor Agent: generates daily AI industry reports from scored news and trends.

Assembles a multi-section Markdown report covering:
- Detected trends and bursts
- Key events (S/A-tier)
- Signal hotspots (B-tier with high community engagement)
- Tag-based industry insights
- Tomorrow's watchlist
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


class EditorAgent:
    """Assembles daily AI industry report from scored articles and detected trends.

    从已评分、去重后的文章和趋势检测结果中生成一份结构化的 Markdown 日报，
    包含趋势信号、关键事件、信号热点、行业解读和明日观察等板块。
    """

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.report_title = self.config.get("report_title", "AI 行业日报")
        self.default_top_n = self.config.get("top_n", 20)

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def generate_report(
        self,
        articles: list[dict],
        trends: dict | None = None,
        *,
        top_n: int | None = None,
    ) -> str:
        """Generate a Markdown-formatted daily report.

        Args:
            articles: Scored and deduplicated articles (must have signal_score,
                      signal_level, tags, etc.)
            trends: Trend analysis results from TrendDetector.analyze()
            top_n: Max articles per section (defaults to self.default_top_n)

        Returns:
            Markdown string suitable for publishing or archival.
        """
        n = top_n if top_n is not None else self.default_top_n

        if not articles:
            logger.warning("[EditorAgent] No articles provided; generating empty report.")
            return self._empty_report()

        # 按 signal_score 降序排列
        scored = sorted(
            articles,
            key=lambda a: (
                float(a.get("signal_score", 0)),
                float(a.get("hotness_score", 0)),
            ),
            reverse=True,
        )

        sections: list[str] = []

        # ---- Header ----
        today = datetime.now().strftime("%Y年%m月%d日")
        sections.append(f"# {self.report_title}")
        sections.append(f"**{today}** | 共 {len(articles)} 条 AI 相关新闻")
        sections.append("")

        # ---- Section 1: Trends ----
        sections.append("##  趋势信号")
        if trends and trends.get("bursts"):
            sections.append(self._format_trends(trends))
        else:
            sections.append("_今日无显著突发趋势_")
        sections.append("")

        # ---- Section 2: Key Events (S-tier + A-tier) ----
        sections.append("##  关键事件")
        key_events = [
            a for a in scored
            if a.get("signal_level") in ("S", "A")
        ][:n]
        if key_events:
            sections.append(self._format_key_events(key_events))
        else:
            sections.append("_暂无 S/A 级关键事件_")
        sections.append("")

        # ---- Section 3: Signal Hotspots (B-tier with community signal) ----
        sections.append("##  信号热点")
        hotspots = [
            a for a in scored
            if a.get("signal_level") == "B"
            and float(a.get("hotness_score", 0)) > 100
        ][:n]
        if hotspots:
            sections.append(self._format_high_signal(hotspots))
        else:
            sections.append("_暂无高信号热点_")
        sections.append("")

        # ---- Section 4: Industry Insights ----
        sections.append("##  行业解读")
        sections.append(self._generate_insights(scored[:n], trends))
        sections.append("")

        # ---- Section 5: Tomorrow's Watchlist ----
        sections.append("##  明日观察")
        sections.append(self._generate_watchlist(scored[:n]))
        sections.append("")

        # ---- Footer ----
        sections.append("---")
        sections.append(f"_AI News Radar V3 · 自动生成于 {today}_")

        return "\n".join(sections)

    # ------------------------------------------------------------------
    # 板块格式化
    # ------------------------------------------------------------------

    def _format_trends(self, trends: dict) -> str:
        """Format trend/burst information."""
        lines: list[str] = []
        bursts = trends.get("bursts", [])
        if not bursts:
            return "_无突发趋势_"

        for b in bursts[:5]:
            topic = str(b.get("topic") or "Unknown")
            burst_score = float(b.get("burst_score", 0))
            current_count = int(b.get("current_count", 0))
            status = str(b.get("status", "steady"))

            status_emoji = ""
            if status == "breaking":
                status_emoji = ""
            elif status == "rising":
                status_emoji = ""

            lines.append(
                f"- {status_emoji} **{topic}** "
                f"(突发指数: {burst_score:.1f}x, "
                f"今日 {current_count} 条)"
            )
        return "\n".join(lines)

    def _format_key_events(self, articles: list[dict]) -> str:
        """Format S/A-tier key events as detailed entries with TL;DR."""
        lines: list[str] = []
        for a in articles[:10]:
            title = str(a.get("title_zh") or a.get("title") or "Unknown")
            level = str(a.get("signal_level") or "B")
            tldr = str(a.get("tldr") or "")
            url = str(a.get("url") or "#")
            source = str(a.get("source") or a.get("site_name") or "")

            level_emoji = {"S": "", "A": ""}.get(level, "")

            lines.append(f"### {level_emoji} [{level}] {title}")
            if tldr:
                lines.append(f"> {tldr}")
            lines.append(f"来源: {source} | [阅读原文]({url})")
            lines.append("")
        return "\n".join(lines)

    def _format_high_signal(self, articles: list[dict]) -> str:
        """Format B-tier high-signal hotspots as compact list items."""
        lines: list[str] = []
        for a in articles[:10]:
            title = str(a.get("title_zh") or a.get("title") or "Unknown")
            tldr = str(a.get("tldr") or "")
            url = str(a.get("url") or "#")

            tags = [str(t).strip() for t in (a.get("tags") or []) if str(t).strip()]
            tag_str = " · ".join(tags[:3])

            line = f"- [{title}]({url})"
            if tldr:
                line += f" -- _{tldr}_"
            if tag_str:
                line += f"  `{tag_str}`"
            lines.append(line)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 行业解读
    # ------------------------------------------------------------------

    def _generate_insights(
        self, articles: list[dict], trends: dict | None
    ) -> str:
        """Generate industry insights from tag distribution and source diversity."""
        parts: list[str] = []

        # 话题分布分析
        tag_counts: dict[str, int] = {}
        for a in articles:
            for tag in a.get("tags", []):
                tag = str(tag).strip()
                if tag:
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1

        top_tags = sorted(tag_counts, key=tag_counts.get, reverse=True)[:5]
        if top_tags:
            parts.append("**今日话题分布:**")
            parts.append("、".join(f"{t}({tag_counts[t]})" for t in top_tags))
        else:
            parts.append("**今日话题分布:** 暂无足够数据")

        # 信源多样性
        sources: set[str] = set()
        for a in articles:
            sid = str(a.get("site_id") or "")
            if sid:
                sources.add(sid)
        parts.append(f"**信源覆盖:** {len(sources)} 个独立来源")

        # 趋势信号
        if trends and trends.get("bursts"):
            burst_count = len(trends["bursts"])
            parts.append(f"**趋势信号:** 检测到 {burst_count} 个可能的重要趋势变化")
        else:
            parts.append("**趋势信号:** 今日无显著突发趋势")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # 明日观察
    # ------------------------------------------------------------------

    def _generate_watchlist(self, articles: list[dict]) -> str:
        """Generate tomorrow's watchlist by identifying diverse topics."""
        lines = ["基于今日信号，建议明日关注以下方向：", ""]

        seen_tags: set[str] = set()
        for a in articles:
            tags = a.get("tags", [])
            if not tags:
                continue
            for tag in tags:
                tag = str(tag).strip()
                if not tag or tag in seen_tags:
                    continue
                title = str(a.get("title_zh") or a.get("title") or "")
                lines.append(f"- **{tag}**: {title[:60]}")
                seen_tags.add(tag)
                if len(seen_tags) >= 5:
                    break
            if len(seen_tags) >= 5:
                break

        if len(seen_tags) == 0:
            lines.append("- 今日暂无明确方向，建议关注 S 级事件的发展")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Fallback
    # ------------------------------------------------------------------

    def _empty_report(self) -> str:
        """Generate a minimal report when no articles are available."""
        today = datetime.now().strftime("%Y年%m月%d日")
        return (
            f"# {self.report_title}\n\n"
            f"**{today}** | 共 0 条 AI 相关新闻\n\n"
            "_今日暂无数据，请检查数据采集流水线是否正常运行。_\n\n"
            "---\n"
            f"_AI News Radar V3 · 自动生成于 {today}_"
        )

"""Trend Agent: natural-language interpretation of detected trends.

Converts raw trend detection output (clusters, bursts, scores) into
human-readable insights with significance assessments and suggested actions.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class TrendAgent:
    """Analyzes and interprets detected trends into human-readable insights.

    将 TrendDetector.analyze() 的原始输出（聚类 + 突发检测结果）转换为
    结构化的趋势洞察，包含话题摘要、意义评估和行动建议。
    """

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.min_cluster_size = self.config.get("min_cluster_size", 5)
        self.max_insights = self.config.get("max_insights", 10)

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def analyze_trends(self, trend_result: dict) -> list[dict]:
        """Convert raw trend data into structured insights.

        Args:
            trend_result: Output from TrendDetector.analyze(), expected keys:
                - clusters: list[dict] — 聚类结果，每项有 topic, size, items
                - bursts: list[dict] — 突发检测结果，每项有 topic, burst_score, status, items

        Returns:
            list of insight dicts:
                {
                    "topic": str,           # 话题名称
                    "summary": str,          # 一段中文摘要
                    "significance": str,     # 重要性评估
                    "action_items": list[str],  # 行动建议
                    "burst_score": float,    # 突发指数
                    "status": str,           # breaking / rising / steady
                }
        """
        if not trend_result:
            logger.info("[TrendAgent] Empty trend result; returning no insights.")
            return []

        insights: list[dict] = []

        clusters = trend_result.get("clusters", [])
        bursts = trend_result.get("bursts", [])

        if not clusters and not bursts:
            logger.info("[TrendAgent] No clusters or bursts to analyze.")
            return []

        # Step 1: 分析突发话题（优先级最高）
        for burst in bursts:
            if not isinstance(burst, dict):
                continue

            topic = str(burst.get("topic", "")).strip()
            if not topic:
                continue

            items = burst.get("items", [])
            if not isinstance(items, list):
                items = []

            insight = {
                "topic": topic,
                "summary": self._summarize_cluster(topic, items),
                "significance": self._assess_significance(burst, items),
                "action_items": self._suggest_actions(items),
                "burst_score": float(burst.get("burst_score", 0)),
                "status": str(burst.get("status", "steady")),
            }
            insights.append(insight)

        # Step 2: 分析大聚类（未被突发覆盖的持续关注话题）
        burst_topics = {str(b.get("topic", "")) for b in bursts}
        for cluster in clusters:
            if not isinstance(cluster, dict):
                continue

            topic = str(cluster.get("topic", "")).strip()
            if not topic:
                continue

            if topic in burst_topics:
                continue  # 已在突发中覆盖

            size = int(cluster.get("size", 0))
            if size < self.min_cluster_size:
                continue

            items = cluster.get("items", [])
            if not isinstance(items, list):
                items = []

            insights.append({
                "topic": topic,
                "summary": self._summarize_cluster(topic, items),
                "significance": "持续关注话题，覆盖多个来源",
                "action_items": [],
                "burst_score": 0.0,
                "status": "ongoing",
            })

        # Step 3: 按突发指数排序，限制数量
        insights.sort(key=lambda i: i["burst_score"], reverse=True)
        if len(insights) > self.max_insights:
            insights = insights[: self.max_insights]

        logger.info("[TrendAgent] Generated %d trend insights.", len(insights))
        return insights

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _summarize_cluster(self, topic: str, items: list[dict]) -> str:
        """基于聚类内的文章标题为话题生成一段中文摘要。

        同一话题下的文章标题可能存在重复（去重合并的副作用），
        这里提取前几条不重复的标题来构建摘要。
        """
        if not items:
            return f"关于 {topic} 的讨论增多"

        seen_titles: set[str] = set()
        unique_titles: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title_zh") or item.get("title", "")).strip()
            if title and title not in seen_titles:
                seen_titles.add(title)
                unique_titles.append(title)
            if len(unique_titles) >= 3:
                break

        if not unique_titles:
            return f"关于 {topic} 的讨论热度上升"

        if len(unique_titles) == 1:
            return f"围绕 {topic}: {unique_titles[0][:80]}"

        return f"围绕 {topic} 的最新动态: {'; '.join(unique_titles[:2])}"

    def _assess_significance(self, burst: dict, items: list[dict]) -> str:
        """评估趋势的重要性和影响程度。"""
        score = float(burst.get("burst_score", 1))
        status = str(burst.get("status", "steady"))

        if status == "breaking" and score >= 3:
            return "重大突发事件，可能对行业格局产生显著影响，建议立即关注"
        elif status == "breaking":
            return "突发趋势，短期内有重要新闻密集发布"
        elif status == "rising":
            return "话题热度持续上升，可能酝酿重要变化"
        elif status == "declining":
            return "话题热度正在下降，关注后续发展"
        return "常规关注话题，持续跟踪即可"

    def _suggest_actions(self, items: list[dict]) -> list[str]:
        """根据聚类中文章的标签，给出具体的行动建议。"""
        actions: list[str] = []
        tag_action_map = {
            "模型发布": "评估新模型能力、价格和 API 可用性",
            "编码工具": "测试新工具对现有开发工作流的影响",
            "论文研究": "阅读论文，判断技术路线趋势",
            "开源": "检查项目代码、协议和社区活跃度",
            "行业动态": "分析资金和生态格局变化的影响",
            "融资": "关注该赛道资金流向和创业机会",
            "产品发布": "评估产品差异化竞争力与市场定位",
            "安全": "检查是否有安全漏洞或隐私影响需关注",
            "政策": "跟踪法规走向，评估合规成本",
            "多模态": "关注多模态能力进展和产品化落地",
            "Agent": "评估智能体架构在自身业务中的应用可能",
        }

        tags_seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            for tag in item.get("tags", []):
                tag = str(tag).strip()
                if not tag or tag in tags_seen:
                    continue
                tags_seen.add(tag)

                action = tag_action_map.get(tag)
                if action and action not in actions:
                    actions.append(action)

            if len(actions) >= 3:
                break

        if not actions:
            actions.append("持续跟踪该话题的最新进展")

        return actions[:3]

"""突发检测：识别短期内讨论量激增的话题。

将当前聚类结果中的各话题数量与历史数据中的平均每日数量比较，
计算突发分数（current / avg_7d），按阈值分为 breaking / rising / steady 三级。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class BurstDetector:
    """检测突发增长的话题。"""

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        # 突发阈值：当前/历史平均 >= 2.0 视为 breaking
        self.burst_threshold = self.config.get("burst_threshold", 2.0)
        # 历史数据回溯天数
        self.history_window_days = self.config.get("history_window_days", 7)

    def detect(
        self,
        clusters: list[dict],
        cluster_history: list[dict] | None = None,
    ) -> list[dict]:
        """检测哪些话题正在突发增长。

        Args:
            clusters: 当前聚类结果（来自 TrendClustering.cluster()）
            cluster_history: 过去若干天的聚类历史数据，用于计算基线

        Returns:
            突发提醒列表，每个元素包含 topic, burst_score, status 等字段
        """
        if not clusters:
            return []

        bursts: list[dict] = []

        for cluster in clusters:
            topic = cluster.get("topic", "Unknown")
            current = cluster.get("size", len(cluster.get("items", [])))

            # 从历史数据中获取该话题的平均每日数量
            avg = (
                self._get_historical_avg(topic, cluster_history)
                if cluster_history
                else 0
            )

            if avg > 0:
                score = current / max(avg, 1)
            else:
                # 新话题无历史数据：数量够大才算显著
                score = min(float(current), 3.0)

            status = self._classify(score)
            if status != "steady":
                bursts.append(
                    {
                        "topic": topic,
                        "current_count": current,
                        "avg_7d": round(avg, 1),
                        "burst_score": round(score, 2),
                        "status": status,
                        "items": cluster.get("items", []),
                    }
                )

        bursts.sort(key=lambda b: b["burst_score"], reverse=True)
        return bursts

    def _get_historical_avg(
        self, topic: str, cluster_history: list[dict]
    ) -> float:
        """从历史聚类数据中计算某话题的日均文章数。"""
        if not cluster_history:
            return 0.0

        counts = []
        for day_data in cluster_history:
            for cluster in day_data.get("clusters", []):
                if cluster.get("topic") == topic:
                    counts.append(cluster.get("size", 0))

        if not counts:
            return 0.0
        return sum(counts) / len(counts)

    def _classify(self, score: float) -> str:
        """按突发分数分级：breaking / rising / steady。"""
        if score >= self.burst_threshold:
            return "breaking"
        elif score >= 1.5:
            return "rising"
        return "steady"

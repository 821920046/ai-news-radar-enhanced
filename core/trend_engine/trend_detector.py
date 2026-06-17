"""趋势检测编排器：串联 聚类 → 突发检测 的完整流水线。

TrendDetector 是趋势引擎的顶层入口，负责：
1. 调用 TrendClustering 对文章进行语义聚类
2. 调用 BurstDetector 检测突发话题
3. 维护聚类历史（供后续运行做基线比较）
4. 通过 TREND_ENGINE_ENABLED 环境变量控制开关
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from core.trend_engine.clustering import TrendClustering
from core.trend_engine.burst_detection import BurstDetector

logger = logging.getLogger(__name__)


class TrendDetector:
    """趋势检测编排器：embed → cluster → detect bursts。"""

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.clustering = TrendClustering(config.get("clustering", {}))
        self.burst_detector = BurstDetector(config.get("burst", {}))
        # 聚类历史，供 BurstDetector 做基线比较
        self.cluster_history: list[dict] = []

    def analyze(
        self,
        articles: list[dict],
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """运行完整的趋势分析流水线。

        Returns:
            {
                "clusters": [...],      # 聚类结果
                "bursts": [...],        # 突发检测结果
                "trend_count": int,     # 检测到的突发话题数
                "total_clustered": int, # 被聚类的文章总数
            }
        """
        # Step 1: 语义聚类
        clusters = self.clustering.cluster(articles, api_key=api_key)

        # Step 2: 突发检测
        bursts = self.burst_detector.detect(clusters, self.cluster_history)

        # Step 3: 存入历史（供后续运行做基线）
        self.cluster_history.append(
            {
                "date": datetime.now(timezone.utc).isoformat(),
                "clusters": [
                    {"topic": c["topic"], "size": c["size"]} for c in clusters
                ],
            }
        )

        # 仅保留最近 N 天的历史
        max_history = self.config.get("max_history", 7)
        if len(self.cluster_history) > max_history:
            self.cluster_history = self.cluster_history[-max_history:]

        return {
            "clusters": clusters,
            "bursts": bursts,
            "trend_count": len(bursts),
            "total_clustered": sum(c.get("size", 0) for c in clusters),
        }

    def is_enabled(self) -> bool:
        """通过环境变量 TREND_ENGINE_ENABLED 控制特性开关。"""
        import os

        return (
            os.environ.get("TREND_ENGINE_ENABLED", "").strip().lower()
            in {"1", "true", "yes", "on"}
        )

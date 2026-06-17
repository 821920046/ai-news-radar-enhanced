"""趋势引擎（Trend Engine）—— 基于语义嵌入聚类的话题发现与突发检测模块。

将每日新闻文章通过 OpenRouter embeddings API 生成向量，用余弦相似度聚类成话题，
并结合历史频次检测突发性增长的趋势话题，追踪话题生命周期（new → growing → peaking → declining）。

V3 升级：从 V2 的标签聚类升级为基于 embedding 的语义聚类。
"""

from __future__ import annotations

from .clustering import TrendClustering
from .burst_detection import BurstDetector
from .trend_detector import TrendDetector
from .evolution import TrendEvolution

__all__ = [
    "TrendClustering",
    "BurstDetector",
    "TrendDetector",
    "TrendEvolution",
]

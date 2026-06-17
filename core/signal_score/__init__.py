"""Signal Score 2.0 — 多维信号评分引擎。

升级自 V1 的简单 60-99 展示分（scripts/recommend.py 中的 build_signal_score），
V2 基于 5 个维度（信源权重、技术深度、新颖度、传播速度、社区信号）进行加权评分，
输出 S/A/B/C 四级分层和细分 breakdown。
"""

from __future__ import annotations

from .scorer import SignalScoreEngine
from .features import compute_velocity, compute_novelty, compute_community_signal
from .feedback import FeedbackCollector
from .outcome_tracker import OutcomeTracker

__all__ = [
    "SignalScoreEngine",
    "compute_velocity",
    "compute_novelty",
    "compute_community_signal",
    "FeedbackCollector",
    "OutcomeTracker",
]

"""趋势生命周期追踪：记录每个话题从出现到衰退的演化过程。

追踪每个话题的四个阶段：
- new：首次出现
- growing：文章数持续增长
- peaking：处于高峰期（当前数量 >= 峰值的 70%）
- declining：数量显著下降（低于峰值的 70%）

超过 14 天未再出现的话题自动清理。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


class TrendEvolution:
    """追踪趋势话题在多次运行中的生命周期变化。"""

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        # 按话题名索引的状态快照
        self.trend_states: dict[str, dict[str, Any]] = {}

    def update(
        self, bursts: list[dict], clusters: list[dict]
    ) -> dict[str, str]:
        """根据当前突发检测结果更新各话题的生命周期阶段。

        Returns:
            {topic: lifecycle_stage} 映射，stage 为 new/growing/peaking/declining
        """
        lifecycle: dict[str, str] = {}

        for burst in bursts:
            topic = burst.get("topic", "")
            if not topic:
                continue

            if topic not in self.trend_states:
                # 首次出现
                self.trend_states[topic] = {
                    "first_seen": datetime.now(timezone.utc).isoformat(),
                    "stage": "new",
                    "peak_count": burst.get("current_count", 0),
                }
                lifecycle[topic] = "new"
            else:
                state = self.trend_states[topic]
                current = burst.get("current_count", 0)
                peak = state.get("peak_count", 0)

                if current > peak:
                    state["peak_count"] = current
                    state["stage"] = "growing"
                elif current >= peak * 0.7:
                    state["stage"] = "peaking"
                else:
                    state["stage"] = "declining"

                state["last_seen"] = datetime.now(timezone.utc).isoformat()
                lifecycle[topic] = state["stage"]

        return lifecycle

    def get_active_trends(self) -> list[dict]:
        """获取当前活跃（非 declining）的趋势话题列表。"""
        return [
            {"topic": topic, **state}
            for topic, state in self.trend_states.items()
            if state.get("stage") != "declining"
        ]

    def prune_stale(self, max_age_days: int = 14) -> int:
        """清理超过 max_age_days 天未再出现的话题，返回清理数量。"""
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        stale = []
        for topic, state in self.trend_states.items():
            last = state.get("last_seen", state.get("first_seen", ""))
            try:
                last_dt = datetime.fromisoformat(last)
                if last_dt < cutoff:
                    stale.append(topic)
            except (ValueError, TypeError):
                stale.append(topic)

        for topic in stale:
            del self.trend_states[topic]

        if stale:
            logger.info("Pruned %d stale trends.", len(stale))
        return len(stale)

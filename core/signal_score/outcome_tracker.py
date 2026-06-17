"""结果追踪 — 追踪高信号文章是否带来了真实世界的结果。

当前为 stub 实现，内存存储。后续可扩展为数据库持久化和自动关联追踪。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from core.utils import utc_now

logger = logging.getLogger(__name__)


class OutcomeTracker:
    """追踪高信号文章的下游结果。

    例如：
    - product_launched：文章预测的产品最终是否发布
    - paper_cited：论文后续被引用情况
    - model_released：模型最终是否开源/发布
    - funding_confirmed：融资传闻最终是否确认
    - trend_materialized：趋势预测是否成真

    用于关闭信号评分的验证回路：高分文章最终应该更常产生实际结果。
    """

    def __init__(self):
        self.outcomes: list[dict[str, Any]] = []

    def track(
        self,
        article_id: str,
        outcome_type: str,
        notes: str = "",
        signal_score: float | None = None,
    ) -> None:
        """记录一条下游结果。

        Args:
            article_id: 相关文章 ID。
            outcome_type: 结果类型（product_launched / paper_cited / model_released
                          / funding_confirmed / trend_materialized / other）。
            notes: 备注说明。
            signal_score: 可选的文章当时信号分数，用于后续关联分析。
        """
        record: dict[str, Any] = {
            "article_id": str(article_id),
            "outcome_type": str(outcome_type),
            "notes": str(notes),
            "recorded_at": utc_now().isoformat(),
        }
        if signal_score is not None:
            record["signal_score"] = signal_score

        self.outcomes.append(record)
        logger.info(
            "追踪结果: article=%s type=%s score=%s",
            article_id,
            outcome_type,
            signal_score,
        )

    def get_by_article(self, article_id: str) -> list[dict[str, Any]]:
        """获取某篇文章的所有下游结果。

        Args:
            article_id: 文章 ID。

        Returns:
            该文章的所有结果记录列表。
        """
        aid = str(article_id)
        return [o for o in self.outcomes if o["article_id"] == aid]

    def get_summary(self) -> dict[str, Any]:
        """获取追踪摘要统计。

        Returns:
            {
                "total_outcomes": int,
                "by_type": dict[str, int],        # 各类型计数
                "by_level": dict[str, int],       # 各信号层级的结果数
                "match_rate": float | None,       # 高信号（S/A 级）结果占比
            }
        """
        total = len(self.outcomes)
        by_type: dict[str, int] = {}
        high_signal = 0

        for outcome in self.outcomes:
            otype = str(outcome.get("outcome_type", "other"))
            by_type[otype] = by_type.get(otype, 0) + 1

            score = outcome.get("signal_score")
            if score is not None and score > 70:
                high_signal += 1

        return {
            "total_outcomes": total,
            "by_type": by_type,
            "match_rate": round(high_signal / total, 3) if total > 0 else None,
        }

    def to_list(self) -> list[dict[str, Any]]:
        """导出所有结果记录为列表。"""
        return list(self.outcomes)

    def clear(self) -> None:
        """清空所有追踪记录。"""
        self.outcomes.clear()
        logger.info("追踪记录已清空")

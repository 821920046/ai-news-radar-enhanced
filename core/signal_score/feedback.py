"""用户反馈收集 — 收集用户对文章相关性的评分，用于调优信号权重。

当前为 stub 实现，记录到 JSONL 文件。后续可扩展为数据库存储和自动权重调优。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from core.utils import utc_now

logger = logging.getLogger(__name__)


class FeedbackCollector:
    """收集用户对文章相关性的反馈（1-5 评分）。

    反馈数据存储在 JSONL 文件中，每行一条记录。
    后续可用于：
    - 统计各维度的预测准确率
    - 自动调优评分权重
    - A/B 测试不同的权重配置
    """

    def __init__(self, storage_path: Path | None = None):
        """初始化反馈收集器。

        Args:
            storage_path: JSONL 文件路径。默认为 data/feedback.jsonl。
        """
        self.storage_path = storage_path or Path("data/feedback.jsonl")
        self._ensure_storage()

    def _ensure_storage(self) -> None:
        """确保存储目录存在。"""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("无法创建反馈存储目录: %s", e)

    def record(
        self,
        article_id: str,
        rating: int,
        source: str = "api",
        comment: str = "",
    ) -> None:
        """记录一条用户反馈。

        Args:
            article_id: 文章唯一 ID。
            rating: 用户评分 1-5（1=不相关，5=非常重要）。
            source: 反馈来源（api/web/mobile）。
            comment: 可选评语。
        """
        if not isinstance(rating, int) or rating < 1 or rating > 5:
            logger.warning("无效评分 %r，跳过记录", rating)
            return

        record_data: dict[str, Any] = {
            "article_id": str(article_id),
            "rating": rating,
            "source": str(source),
            "comment": str(comment),
            "recorded_at": utc_now().isoformat(),
        }

        try:
            with open(self.storage_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record_data, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.error("写入反馈数据失败: %s", e)

    def get_stats(self) -> dict[str, Any]:
        """获取聚合反馈统计。

        Returns:
            {
                "total": int,       # 总反馈数
                "avg_rating": float, # 平均评分
                "by_rating": dict,  # 各评分的计数 {1: n, 2: n, ...}
                "by_source": dict,  # 各来源的计数
            }
        """
        if not self.storage_path.exists():
            return {"total": 0, "avg_rating": 0.0, "by_rating": {}, "by_source": {}}

        total = 0
        rating_sum = 0
        by_rating: dict[int, int] = {}
        by_source: dict[str, int] = {}

        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    total += 1
                    rating = record.get("rating", 0)
                    rating_sum += rating
                    by_rating[rating] = by_rating.get(rating, 0) + 1

                    source = str(record.get("source", "unknown"))
                    by_source[source] = by_source.get(source, 0) + 1
        except OSError as e:
            logger.error("读取反馈数据失败: %s", e)
            return {"total": 0, "avg_rating": 0.0, "by_rating": {}, "by_source": {}}

        return {
            "total": total,
            "avg_rating": round(rating_sum / total, 2) if total > 0 else 0.0,
            "by_rating": by_rating,
            "by_source": by_source,
        }

    def load_all(self) -> list[dict[str, Any]]:
        """加载所有反馈记录。

        Returns:
            反馈记录列表，每条包含 article_id, rating, source, comment, recorded_at。
        """
        if not self.storage_path.exists():
            return []

        records: list[dict[str, Any]] = []
        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError as e:
            logger.error("读取反馈数据失败: %s", e)

        return records

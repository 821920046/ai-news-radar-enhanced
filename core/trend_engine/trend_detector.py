"""趋势检测编排器：串联 聚类 → 突发检测 的完整流水线。

TrendDetector 是趋势引擎的顶层入口，负责：
1. 调用 TrendClustering 对文章进行语义聚类
2. 调用 BurstDetector 检测突发话题
3. 维护聚类历史（供后续运行做基线比较）
4. 通过 TREND_ENGINE_ENABLED 环境变量控制开关
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.trend_engine.clustering import TrendClustering
from core.trend_engine.burst_detection import BurstDetector

try:
    from core.utils import atomic_write_text
except Exception:  # pragma: no cover - fallback if utils unavailable
    atomic_write_text = None

logger = logging.getLogger(__name__)


class TrendDetector:
    """趋势检测编排器：embed → cluster → detect bursts。"""

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.clustering = TrendClustering(self.config.get("clustering", {}))
        self.burst_detector = BurstDetector(self.config.get("burst", {}))
        # 历史保留天数（与 BurstDetector 的回溯窗口一致）
        self.max_history_days = int(self.config.get("max_history_days", 7))
        # 历史快照硬上限，避免文件无限增长（hourly 运行约 24/天）
        self.max_history_entries = int(self.config.get("max_history_entries", 240))
        # 历史持久化路径：默认 data/trend_history.json，可用 TREND_HISTORY_PATH 覆盖
        self.history_path = (
            self.config.get("history_path")
            or os.environ.get("TREND_HISTORY_PATH")
            or os.path.join(os.environ.get("DATA_DIR", "data"), "trend_history.json")
        )
        # 聚类历史，供 BurstDetector 做基线比较（从磁盘加载，实现跨运行持久化）
        self.cluster_history: list[dict] = self._load_history()

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

        # Step 3: 存入历史（供后续运行做基线），并持久化到磁盘
        self.cluster_history.append(
            {
                "date": datetime.now(timezone.utc).isoformat(),
                "clusters": [
                    {"topic": c["topic"], "size": c["size"]} for c in clusters
                ],
            }
        )
        # 按时间窗口裁剪（保留最近 N 天），再施加硬上限，最后写回磁盘
        self.cluster_history = self._prune_history(self.cluster_history)
        self._save_history(self.cluster_history)

        return {
            "clusters": clusters,
            "bursts": bursts,
            "trend_count": len(bursts),
            "total_clustered": sum(c.get("size", 0) for c in clusters),
        }

    def _load_history(self) -> list[dict]:
        """从磁盘加载历史聚类快照；文件不存在或损坏时返回空列表。"""
        try:
            path = Path(self.history_path)
            if not path.exists():
                return []
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return self._prune_history([d for d in data if isinstance(d, dict)])
        except Exception as exc:
            logger.warning(
                "[TrendEngine] Failed to load history from %s: %s", self.history_path, exc
            )
        return []

    def _prune_history(self, history: list[dict]) -> list[dict]:
        """按时间窗口和硬上限裁剪历史快照。"""
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.max_history_days)
        kept: list[dict] = []
        for entry in history:
            ts = entry.get("date")
            try:
                dt = datetime.fromisoformat(ts) if ts else None
                if dt is not None and dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except Exception:
                dt = None
            # 无法解析时间的旧快照保守保留
            if dt is None or dt >= cutoff:
                kept.append(entry)
        if len(kept) > self.max_history_entries:
            kept = kept[-self.max_history_entries:]
        return kept

    def _save_history(self, history: list[dict]) -> None:
        """原子写入历史快照到磁盘。"""
        try:
            path = Path(self.history_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(history, ensure_ascii=False, indent=2)
            if atomic_write_text is not None:
                atomic_write_text(path, payload)
            else:
                path.write_text(payload, encoding="utf-8")
        except Exception as exc:
            logger.warning(
                "[TrendEngine] Failed to save history to %s: %s", self.history_path, exc
            )

    def is_enabled(self) -> bool:
        """通过环境变量 TREND_ENGINE_ENABLED 控制特性开关。"""
        import os

        return (
            os.environ.get("TREND_ENGINE_ENABLED", "").strip().lower()
            in {"1", "true", "yes", "on"}
        )

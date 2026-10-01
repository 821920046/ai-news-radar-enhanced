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
from typing import Any, Iterable

from core.trend_engine.clustering import TrendClustering
from core.trend_engine.burst_detection import BurstDetector
from core.trend_engine.heat import HeatEngine, detect_behind_sources

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
        self.heat_engine = HeatEngine(self.config.get("heat", {}))
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
        *,
        at: datetime | None = None,
        expected_site_ids: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        """运行完整的趋势分析流水线。

        Args:
            articles: 本轮全部内容。
            api_key: embedding 用的 API key（TF-IDF 模式下忽略）。
            at: 热度计算的基准时刻，默认当前 UTC。
            expected_site_ids: 本轮理论上应该在场的信源 id 集合。传入后，
                缺席的源会被识别为「未跟上」，相关事件的趋势标记为
                ``unknown`` 而非 ``down`` —— 避免把「没抓到」误报成
                「讨论变少」。

        Returns:
            {
                "clusters": [...],      # 聚类结果（每个簇附加 heat_* 字段）
                "bursts": [...],        # 突发检测结果
                "trend_count": int,     # 检测到的突发话题数
                "total_clustered": int, # 被聚类的文章总数
                "behind_sources": [...],# 本轮缺席的信源
                "heat_rule": str,       # 热度算法版本
            }
        """
        from datetime import timezone as _tz

        at = at or datetime.now(_tz.utc)

        # Step 1: 语义聚类
        clusters = self.clustering.cluster(articles, api_key=api_key)

        # Step 1.5: 事件级热度
        # 「源未跟上」必须显式识别，否则会把它误判成热度下跌。
        behind: list[str] = []
        if expected_site_ids is not None:
            behind = detect_behind_sources(articles, all_site_ids=expected_site_ids)
            if behind:
                logger.info(
                    "[TrendEngine] %d source(s) produced no evidence this round: %s",
                    len(behind),
                    ", ".join(behind[:8]),
                )
        behind_set = set(behind)
        for cluster in clusters:
            result = self.heat_engine.compute(
                cluster.get("items", []), at=at, behind_sources=behind_set
            )
            cluster.update(result.to_dict())

        # Step 2: 突发检测
        bursts = self.burst_detector.detect(clusters, self.cluster_history)

        # Step 3: 存入历史（供后续运行做基线），并持久化到磁盘
        self.cluster_history.append(
            {
                "date": at.isoformat(),
                "clusters": [
                    {"topic": c["topic"], "size": c["size"]} for c in clusters
                ],
            }
        )
        # 按时间窗口裁剪（保留最近 N 天），再施加硬上限，最后写回磁盘
        self.cluster_history = self._prune_history(self.cluster_history)
        self._save_history(self.cluster_history)

        # 事件热度榜：只保留有多个独立来源的簇（单来源不算「事件热点」）
        hot_events = [
            {
                "topic": c.get("topic", ""),
                "heat": c.get("heat", 0.0),
                "heat_trend": c.get("heat_trend", "unknown"),
                "heat_participants": c.get("heat_participants", 0),
                "heat_badges": c.get("heat_badges", []),
                "heat_sources": c.get("heat_sources", []),
                "size": c.get("size", 0),
            }
            for c in clusters
            if int(c.get("heat_participants") or 0) >= 2
        ]
        hot_events.sort(key=lambda e: -float(e.get("heat") or 0.0))

        return {
            "clusters": clusters,
            "bursts": bursts,
            "hot_events": hot_events,
            "trend_count": len(bursts),
            "total_clustered": sum(c.get("size", 0) for c in clusters),
            "behind_sources": behind,
            "heat_rule": clusters[0].get("heat_rule") if clusters else None,
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
        dropped_unparsable = 0
        for entry in history:
            ts = entry.get("date")
            try:
                dt = datetime.fromisoformat(ts) if ts else None
                if dt is not None and dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except Exception:
                dt = None
            # 无法解析时间的条目转为剔除。
            #
            # 早期实现是「保守保留」，但那样会让损坏条目永久占据配额：
            # 虽然 max_history_entries 把文件体积限制住了，可 240 个槽位一旦被
            # 这些永远匹配不到的条目占满，真正的历史就会被挤出去，
            # 突发检测的基线随之失真。而这类条目本就无法参与时间窗口比较，
            # 保留它们没有任何价值，剔除反而让配额回收到可用的快照上。
            if dt is None:
                dropped_unparsable += 1
                continue
            if dt >= cutoff:
                kept.append(entry)
        if dropped_unparsable:
            logger.info(
                "[TrendEngine] Dropped %d history entries with unusable dates.", dropped_unparsable
            )
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

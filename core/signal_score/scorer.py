"""Signal Score 2.0 核心评分引擎（YAML 可配置版）。

对每篇文章进行 5 维度（信源权重、技术深度、新颖度、传播速度、社区信号）
加权评分，输出 S/A/B/C 四级分层和细分 breakdown。

相对旧版的改进：
- 权重 / 等级阈值 / 信源权威度 / 技术加分 全部从 config/score_weights.yaml 读取，
  不再硬编码魔法数；配置缺失或损坏时回退到内置默认值。
- 权重自动归一化，配置笔误不会破坏总分尺度。
- 新增 percentile（动态分位数）分级模式，可缓解分数通胀导致的等级失真。
- score_batch 结束后输出 S/A/B/C 分布日志，便于监控评分健康度。

纯规则驱动，无需 OpenRouter / API Key。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from core.utils import compute_hotness, strip_html_tags

from .features import compute_velocity, compute_novelty, compute_community_signal

logger = logging.getLogger(__name__)

# 标题中的数字/百分比/版本号模式
_NUMBER_SPECIFIC_RE = re.compile(
    r"(?:\d+\.\d+\.\d+|\d+\.\d+|\d+%|\d+\s*(?:亿|万|B|M|K|\$)\b|\bv\d+)",
    re.I,
)

# ── 内置默认配置（config/score_weights.yaml 缺失时回退）─────────────────────
_DEFAULT_CONFIG: dict[str, Any] = {
    "weights": {
        "source_weight": 0.25,
        "technical_score": 0.25,
        "novelty": 0.20,
        "velocity": 0.15,
        "community": 0.15,
    },
    "levels": {
        "mode": "static",
        "thresholds": {"S": 85, "A": 70, "B": 50},
        "percentiles": {"S": 0.90, "A": 0.70, "B": 0.40},
    },
    "source_authority": {
        "official_ai": 100,
        "aibreakfast": 85,
        "followbuilders": 85,
        "oss_trending": 85,
        "buzzing": 70,
        "zeli": 70,
        "newsnow": 70,
    },
    "technical_scoring": {
        "tldr_bonus": 30,
        "long_description_bonus": 25,
        "long_description_min_chars": 200,
        "depth_tag_bonus": 25,
        "number_in_title_bonus": 20,
        "depth_tags": ["论文研究", "模型发布", "部署推理"],
    },
    "unknown_source_hotness_divisor": 10,
}

_DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent.parent / "config" / "score_weights.yaml"
)


def _deep_merge(base: dict, override: dict) -> dict:
    """浅层 + 一级嵌套合并：override 覆盖 base。"""
    merged = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            sub = dict(merged[key])
            sub.update(value)
            merged[key] = sub
        else:
            merged[key] = value
    return merged


def load_score_config(path: str | Path | None = None) -> dict[str, Any]:
    """从 YAML 读取评分配置，缺失/损坏时回退到默认配置。"""
    cfg_path = Path(path) if path else _DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        return {k: (dict(v) if isinstance(v, dict) else v) for k, v in _DEFAULT_CONFIG.items()}
    try:
        import yaml  # 延迟导入：未安装 pyyaml 时仍可用默认配置

        with cfg_path.open("r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
    except Exception:
        logger.warning("加载 %s 失败，使用默认评分配置", cfg_path, exc_info=True)
        return {k: (dict(v) if isinstance(v, dict) else v) for k, v in _DEFAULT_CONFIG.items()}
    return _deep_merge(_DEFAULT_CONFIG, loaded)


class SignalScoreEngine:
    """多维信号评分引擎（5 维加权 + S/A/B/C 分层）。"""

    def __init__(self, config: dict | None = None):
        """初始化评分引擎。

        Args:
            config: 可选覆盖配置。会覆盖 YAML/默认配置中的同名键，
                    并兼容旧用法（直接传 weights / source_authority / archive）。
        """
        base = load_score_config()
        base = _deep_merge(base, config or {})
        self.config = base

        self.weights = self._normalize_weights(base.get("weights", {}))
        self.source_authority = dict(base.get("source_authority", {}))
        self.archive = base.get("archive")

        tech = base.get("technical_scoring", {})
        self._tldr_bonus = float(tech.get("tldr_bonus", 30))
        self._long_desc_bonus = float(tech.get("long_description_bonus", 25))
        self._long_desc_min = int(tech.get("long_description_min_chars", 200))
        self._depth_tag_bonus = float(tech.get("depth_tag_bonus", 25))
        self._number_bonus = float(tech.get("number_in_title_bonus", 20))
        self._depth_tags = set(tech.get("depth_tags", []))

        divisor = float(base.get("unknown_source_hotness_divisor", 10) or 10)
        self._hotness_divisor = divisor if divisor > 0 else 10.0

        levels = base.get("levels", {})
        self._level_mode = str(levels.get("mode", "static")).lower()
        self._static_thresholds = dict(levels.get("thresholds", {"S": 85, "A": 70, "B": 50}))
        self._percentiles = dict(levels.get("percentiles", {"S": 0.90, "A": 0.70, "B": 0.40}))

    # ------------------------------------------------------------------
    # 配置辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_weights(weights: dict) -> dict:
        clean = {k: float(v) for k, v in (weights or {}).items() if isinstance(v, (int, float))}
        total = sum(clean.values())
        if total <= 0:
            return dict(_DEFAULT_CONFIG["weights"])
        return {k: v / total for k, v in clean.items()}

    # ------------------------------------------------------------------
    # 维度 1：信源权威度
    # ------------------------------------------------------------------

    def _score_source_weight(self, article: dict) -> float:
        """根据 site_id 归一化信源权威度。返回 0-100。"""
        site_id = str(article.get("site_id") or "")
        if site_id in self.source_authority:
            return float(self.source_authority[site_id])

        hotness = article.get("hotness_score")
        if hotness is None:
            hotness_score, _ = compute_hotness(article)
            hotness = hotness_score
        return min(100.0, max(0.0, float(hotness) / self._hotness_divisor))

    # ------------------------------------------------------------------
    # 维度 2：技术深度
    # ------------------------------------------------------------------

    def _score_technical(self, article: dict) -> float:
        """评估文章的技术/内容深度。返回 0-100。"""
        score = 0.0

        tldr = article.get("tldr")
        if tldr and str(tldr).strip():
            score += self._tldr_bonus

        description = str(article.get("description") or "")
        clean_desc = strip_html_tags(description)
        if len(clean_desc) > self._long_desc_min:
            score += self._long_desc_bonus

        tags = [str(t) for t in (article.get("tags") or [])]
        if any(tag in self._depth_tags for tag in tags):
            score += self._depth_tag_bonus

        title = str(article.get("title") or "")
        if _NUMBER_SPECIFIC_RE.search(title):
            score += self._number_bonus

        return min(100.0, score)

    # ------------------------------------------------------------------
    # 维度 3：新颖度
    # ------------------------------------------------------------------

    def _score_novelty(self, article: dict) -> float:
        return compute_novelty(article, archive=self.archive)

    # ------------------------------------------------------------------
    # 维度 4：传播速度
    # ------------------------------------------------------------------

    def _score_velocity(self, article: dict, articles: list[dict] | None = None) -> float:
        score = 0.0

        source_count = article.get("source_count")
        if source_count is not None:
            try:
                score += min(100.0, int(source_count) * 25.0)
            except (TypeError, ValueError):
                pass

        if score == 0.0:
            merged_sources = article.get("merged_sources")
            if isinstance(merged_sources, list):
                score += min(100.0, len(merged_sources) * 25.0)

        hotness = article.get("hotness_score")
        if hotness is None:
            hotness_score, _ = compute_hotness(article)
            hotness = hotness_score
        try:
            score += min(100.0, float(hotness) / 10.0)
        except (TypeError, ValueError):
            pass

        if articles and len(articles) > 1:
            velocity_from_peers = compute_velocity(articles, article)
            score = max(score, velocity_from_peers)

        return min(100.0, score)

    # ------------------------------------------------------------------
    # 维度 5：社区信号
    # ------------------------------------------------------------------

    def _score_community(self, article: dict) -> float:
        return compute_community_signal(article)

    # ------------------------------------------------------------------
    # 评分与分层
    # ------------------------------------------------------------------

    def _score_dims(self, article: dict, articles: list[dict] | None) -> dict[str, float]:
        return {
            "source_weight": self._score_source_weight(article),
            "technical_score": self._score_technical(article),
            "novelty": self._score_novelty(article),
            "velocity": self._score_velocity(article, articles),
            "community": self._score_community(article),
        }

    def _weighted_total(self, dims: dict[str, float]) -> float:
        total = 0.0
        for dim, raw_score in dims.items():
            total += raw_score * self.weights.get(dim, 0.0)
        return round(total, 1)

    def _level_for(self, total: float, thresholds: dict) -> str:
        if total > float(thresholds.get("S", 85)):
            return "S"
        if total > float(thresholds.get("A", 70)):
            return "A"
        if total > float(thresholds.get("B", 50)):
            return "B"
        return "C"

    def _percentile_thresholds(self, totals: list[float]) -> dict:
        if not totals:
            return self._static_thresholds
        ordered = sorted(totals)

        def q(p: float) -> float:
            p = min(1.0, max(0.0, float(p)))
            idx = int(round(p * (len(ordered) - 1)))
            return ordered[min(len(ordered) - 1, max(0, idx))]

        return {
            "S": q(self._percentiles.get("S", 0.90)),
            "A": q(self._percentiles.get("A", 0.70)),
            "B": q(self._percentiles.get("B", 0.40)),
        }

    def score(self, article: dict, articles: list[dict] | None = None) -> dict:
        """对单篇文章进行多维信号评分（使用静态阈值分级）。

        Returns:
            {"score": float, "level": "S"|"A"|"B"|"C", "breakdown": {...}}
        """
        dims = self._score_dims(article, articles)
        total = self._weighted_total(dims)
        level = self._level_for(total, self._static_thresholds)
        return {
            "score": total,
            "level": level,
            "breakdown": {dim: round(val, 1) for dim, val in dims.items()},
        }

    def score_batch(self, articles: list[dict]) -> list[dict]:
        """批量评分，在每个 article dict 上原地附加 signal_* 字段。

        根据 levels.mode 选择静态阈值或动态分位数阈值分级。
        """
        computed: list[tuple[dict, float, dict]] = []
        totals: list[float] = []
        for article in articles:
            try:
                dims = self._score_dims(article, articles)
                total = self._weighted_total(dims)
            except Exception:
                logger.exception("signal_score failed for article id=%r", article.get("id"))
                dims, total = {}, 0.0
            computed.append((article, total, dims))
            totals.append(total)

        if self._level_mode == "percentile":
            thresholds = self._percentile_thresholds(totals)
        else:
            thresholds = self._static_thresholds

        distribution = {"S": 0, "A": 0, "B": 0, "C": 0}
        for article, total, dims in computed:
            level = self._level_for(total, thresholds) if dims else "C"
            article["signal_score"] = round(total, 1)
            article["signal_level"] = level
            article["signal_breakdown"] = {dim: round(val, 1) for dim, val in dims.items()}
            distribution[level] = distribution.get(level, 0) + 1

        logger.info(
            "[SignalScore] mode=%s thresholds=%s distribution=%s",
            self._level_mode, thresholds, distribution,
        )
        return articles

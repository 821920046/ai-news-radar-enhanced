"""Signal Score 2.0 核心评分引擎。

对每篇文章进行 5 维度（信源权重、技术深度、新颖度、传播速度、社区信号）
加权评分，输出 S/A/B/C 四级分层和细分 breakdown。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from core.utils import compute_hotness, strip_html_tags

from .features import compute_velocity, compute_novelty, compute_community_signal

logger = logging.getLogger(__name__)

# 技术深度标签关键词
_DEPTH_TAG_KEYWORDS = {
    "论文研究", "模型发布", "部署推理",
}

# 标题中的数字/百分比/版本号模式
_NUMBER_SPECIFIC_RE = re.compile(
    r"(?:\d+\.\d+\.\d+|\d+\.\d+|\d+%|\d+\s*(?:亿|万|B|M|K|\$)\b|\bv\d+)",
    re.I,
)


class SignalScoreEngine:
    """多维信号评分引擎。

    V2 升级自 V1 的简单 60-99 展示分，基于 5 个维度进行加权评分：
    - source_weight：信源权威度
    - technical_score：技术内容深度
    - novelty：内容新颖度
    - velocity：传播扩散速度
    - community：社区参与信号

    纯规则驱动，无需 OpenRouter/API Key。
    每个维度返回 0-100 分，最终加权合成后映射到 S/A/B/C 级。
    """

    # 预设源站点权威度映射
    _SOURCE_AUTHORITY: dict[str | None, int] = {
        "official_ai": 100,
        "aibreakfast": 85,
        "followbuilders": 85,
        "oss_trending": 85,
        "buzzing": 70,
        "zeli": 70,
        "newsnow": 70,
    }

    def __init__(self, config: dict | None = None):
        """初始化评分引擎。

        Args:
            config: 可选配置，支持覆盖 weights 和 source_authority。
                    不传则使用默认权重。
        """
        self.config = config or {}
        self.weights = self.config.get("weights", {
            "source_weight": 0.25,
            "technical_score": 0.25,
            "novelty": 0.20,
            "velocity": 0.15,
            "community": 0.15,
        })
        self.source_authority = self.config.get(
            "source_authority", self._SOURCE_AUTHORITY
        )
        self.archive = self.config.get("archive")

    # ------------------------------------------------------------------
    # 维度 1：信源权威度
    # ------------------------------------------------------------------

    def _score_source_weight(self, article: dict) -> float:
        """根据 site_id 归一化信源权威度。返回 0-100。"""
        site_id = str(article.get("site_id") or "")

        # 检查预设映射
        if site_id in self.source_authority:
            return float(self.source_authority[site_id])

        # 其他信源：从 hotness_score 缩放（0-1000 → 0-100）
        hotness = article.get("hotness_score")
        if hotness is None:
            hotness_score, _ = compute_hotness(article)
            hotness = hotness_score
        return min(100.0, max(0.0, float(hotness) / 10.0))

    # ------------------------------------------------------------------
    # 维度 2：技术深度
    # ------------------------------------------------------------------

    def _score_technical(self, article: dict) -> float:
        """评估文章的技术/内容深度。返回 0-100。"""
        score = 0.0

        # 有 TLDR 摘要 → +30（说明内容有足够的信息量值得提炼）
        tldr = article.get("tldr")
        if tldr and str(tldr).strip():
            score += 30.0

        # 长描述（>200 字符）→ +25（描述越长通常内容越深）
        description = str(article.get("description") or "")
        clean_desc = strip_html_tags(description)
        if len(clean_desc) > 200:
            score += 25.0

        # 技术标签 → +25（论文研究、模型发布、部署推理）
        tags = [str(t) for t in (article.get("tags") or [])]
        for tag in tags:
            if tag in _DEPTH_TAG_KEYWORDS:
                score += 25.0
                break

        # 标题中有具体数字/百分比/版本号 → +20
        title = str(article.get("title") or "")
        if _NUMBER_SPECIFIC_RE.search(title):
            score += 20.0

        return min(100.0, score)

    # ------------------------------------------------------------------
    # 维度 3：新颖度
    # ------------------------------------------------------------------

    def _score_novelty(self, article: dict) -> float:
        """计算文章的新颖度。返回 0-100。"""
        return compute_novelty(article, archive=self.archive)

    # ------------------------------------------------------------------
    # 维度 4：传播速度
    # ------------------------------------------------------------------

    def _score_velocity(self, article: dict, articles: list[dict] | None = None) -> float:
        """计算文章的传播扩散速度。返回 0-100。

        综合两类信号：
        - source_count：去重合并带来的多源计数
        - hotness_score：热度归一化
        """
        score = 0.0

        # source_count（去重合并在原始 pipeline 中产生）
        source_count = article.get("source_count")
        if source_count is not None:
            try:
                sc = int(source_count)
                score += min(100.0, sc * 25.0)
            except (TypeError, ValueError):
                pass

        # 如果没有 source_count，检查 merged_sources 的长度
        if score == 0.0:
            merged_sources = article.get("merged_sources")
            if isinstance(merged_sources, list):
                score += min(100.0, len(merged_sources) * 25.0)

        # hotness_score 归一化
        hotness = article.get("hotness_score")
        if hotness is None:
            hotness_score, _ = compute_hotness(article)
            hotness = hotness_score
        try:
            score += min(100.0, float(hotness) / 10.0)
        except (TypeError, ValueError):
            pass

        # 同类文章传播速度（如果提供了文章列表）
        if articles and len(articles) > 1:
            velocity_from_peers = compute_velocity(articles, article)
            score = max(score, velocity_from_peers)

        return min(100.0, score)

    # ------------------------------------------------------------------
    # 维度 5：社区信号
    # ------------------------------------------------------------------

    def _score_community(self, article: dict) -> float:
        """计算社区参与信号。返回 0-100。"""
        return compute_community_signal(article)

    # ------------------------------------------------------------------
    # 评分与分层
    # ------------------------------------------------------------------

    def score(self, article: dict, articles: list[dict] | None = None) -> dict:
        """对单篇文章进行多维信号评分。

        Args:
            article: 文章数据字典，需包含 title, site_id, tags 等字段。
            articles: 可选的文章列表，用于计算 velocity 的同类传播。
                      传 None 则 velocity 仅基于单篇自身信号。

        Returns:
            {
                "score": float,       # 0-100 加权总分
                "level": "S"|"A"|"B"|"C",
                "breakdown": {        # 各维度细分
                    "source_weight": float,
                    "technical_score": float,
                    "novelty": float,
                    "velocity": float,
                    "community": float,
                }
            }
        """
        # 计算各维度得分
        dims = {
            "source_weight": self._score_source_weight(article),
            "technical_score": self._score_technical(article),
            "novelty": self._score_novelty(article),
            "velocity": self._score_velocity(article, articles),
            "community": self._score_community(article),
        }

        # 加权合成总分
        total = 0.0
        for dim, raw_score in dims.items():
            weight = self.weights.get(dim, 0.0)
            total += raw_score * weight

        total = round(total, 1)

        # 层级映射
        if total > 85:
            level = "S"
        elif total > 70:
            level = "A"
        elif total > 50:
            level = "B"
        else:
            level = "C"

        return {
            "score": total,
            "level": level,
            "breakdown": {
                dim: round(val, 1) for dim, val in dims.items()
            },
        }

    def score_batch(self, articles: list[dict]) -> list[dict]:
        """批量评分，在每个 article dict 上原地附加 signal_score 等字段。

        Args:
            articles: 文章列表。

        Returns:
            同一列表（已原地修改），每篇文章添加：
            - signal_score: float (0-100)
            - signal_level: str (S/A/B/C)
            - signal_breakdown: dict (各维度细分)
        """
        for article in articles:
            try:
                result = self.score(article, articles=articles)
            except Exception:
                logger.exception("signal_score failed for article id=%r", article.get("id"))
                result = {"score": 0.0, "level": "C", "breakdown": {}}

            article["signal_score"] = result["score"]
            article["signal_level"] = result["level"]
            article["signal_breakdown"] = result["breakdown"]

        return articles

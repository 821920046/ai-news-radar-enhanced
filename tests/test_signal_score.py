"""Signal Score 引擎最小单测（可用 pytest 运行）。

这些测试用打桩（monkeypatch）替换 core.utils / features 的依赖，
因此即使在未安装完整 pipeline 依赖的环境也能验证评分/分级逻辑。
"""

from __future__ import annotations

import pytest
from unittest.mock import patch

from core.signal_score.scorer import SignalScoreEngine

@pytest.fixture(autouse=True)
def mock_dependencies():
    """为 scorer 所需的 core.utils / features 注入轻量打桩模块。"""
    with patch("core.signal_score.scorer.compute_hotness", side_effect=lambda article: (float(article.get("hotness_score") or 0.0), {})), \
         patch("core.signal_score.scorer.strip_html_tags", side_effect=lambda s: s), \
         patch("core.signal_score.scorer.compute_velocity", side_effect=lambda articles, article: 0.0), \
         patch("core.signal_score.scorer.compute_novelty", side_effect=lambda article, archive=None: float(article.get("_novelty", 50.0))), \
         patch("core.signal_score.scorer.compute_community_signal", side_effect=lambda article: float(article.get("_community", 50.0))):
        yield

def _engine(**overrides):
    return SignalScoreEngine(overrides or None)


def test_weights_are_normalized():
    eng = _engine(weights={"source_weight": 2, "technical_score": 2,
                           "novelty": 1, "velocity": 1, "community": 1})
    assert abs(sum(eng.weights.values()) - 1.0) < 1e-9


def test_official_source_scores_high():
    eng = _engine()
    article = {"site_id": "official_ai", "title": "GPT-5 v2.0 发布",
               "tldr": "x", "description": "d" * 300, "tags": ["模型发布"],
               "hotness_score": 800, "source_count": 3,
               "_novelty": 90, "_community": 80}
    res = eng.score(article)
    assert res["level"] in {"S", "A"}
    assert set(res["breakdown"]) == {
        "source_weight", "technical_score", "novelty", "velocity", "community"}


def test_low_signal_is_c():
    eng = _engine()
    article = {"site_id": "unknown", "title": "hello", "hotness_score": 0,
               "_novelty": 0, "_community": 0}
    res = eng.score(article)
    assert res["level"] == "C"


def test_score_batch_annotates_and_distributes():
    eng = _engine()
    arts = [
        {"site_id": "official_ai", "title": "v1.2.3", "tldr": "a",
         "description": "d" * 300, "tags": ["论文研究"], "hotness_score": 900,
         "source_count": 4, "_novelty": 95, "_community": 90},
        {"site_id": "unknown", "title": "plain", "hotness_score": 0,
         "_novelty": 0, "_community": 0},
    ]
    eng.score_batch(arts)
    for a in arts:
        assert "signal_score" in a and "signal_level" in a and "signal_breakdown" in a


def test_percentile_mode_splits_levels():
    eng = _engine(levels={"mode": "percentile",
                          "percentiles": {"S": 0.9, "A": 0.6, "B": 0.3}})
    arts = [{"site_id": "unknown", "title": str(i), "hotness_score": i * 100,
             "_novelty": i * 10, "_community": i * 10} for i in range(10)]
    eng.score_batch(arts)
    levels = {a["signal_level"] for a in arts}
    # 动态分级应至少产生两个不同等级
    assert len(levels) >= 2

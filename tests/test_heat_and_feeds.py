"""事件级热度算法（heat v1）与对外出口的回归测试。

覆盖三类容易静默出错的场景：

1. **「源未跟上」不能被报成「热度下跌」** —— 这是本模块存在的首要理由。
   抓取失败导致证据消失时，旧逻辑会把下降报成真实趋势。
2. **同源去重与半衰期** —— 算法正确性的基本盘。
3. **对外出口（RSS / llms.txt）的漂移抑制** —— 否则每小时一个提交，
   把刚治理好的仓库膨胀问题换个文件重演一遍。
"""

from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from core.trend_engine.heat import (  # noqa: E402
    HEAT_RULE_VERSION,
    HeatEngine,
    detect_behind_sources,
    participant_key,
)

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)


def mk(site: str, hours_ago: float, *, name: str | None = None, first_party: bool = False):
    """构造一条距今 `hours_ago` 小时的内容。"""
    return {
        "site_id": site,
        "site_name": name or site,
        "first_party": first_party,
        "published_at": (NOW - timedelta(hours=hours_ago)).isoformat(),
    }


# ── 核心：源未跟上 ≠ 热度下跌 ──────────────────────────────────────────


class TestBehindSourcesNeverReportedAsDecline:
    """这是本次改动要修的核心 bug。"""

    def test_missing_source_yields_unknown_not_down(self):
        """有源未跟上时，趋势必须是 unknown，而不是 down。"""
        engine = HeatEngine()
        # 当前窗口有 4 个来源，但其中 3 个的源本轮没抓到数据
        items = [mk("a", 1), mk("b", 1), mk("c", 1), mk("d", 1)]
        result = engine.compute(items, at=NOW, behind_sources={"a", "b", "c"})

        assert result.comparable is False, "有源缺席时必须标记为不可比"
        assert result.trend == "unknown", (
            f"源缺席时趋势必须是 unknown，实际是 {result.trend!r}；"
            "把它报成 down 会让运维误以为讨论热度下降"
        )
        assert result.trend_pct is None
        assert result.uncomparable_participants > 0

    def test_no_behind_sources_allows_normal_trend(self):
        """没有任何源缺席时，趋势应正常判定（对照实验）。"""
        engine = HeatEngine()
        items = [mk("a", 1), mk("b", 2), mk("c", 3)]
        result = engine.compute(items, at=NOW)  # 不传 behind_sources
        assert result.comparable is True
        assert result.trend in {"up", "down", "flat", "new"}

    def test_trend_still_unknown_even_if_comparable_subset_would_rise(self):
        """即便可比子集显示上涨，只要有源缺席就仍标 unknown。

        保守优先：宁可说「不知道」，也不给出可能误导的方向。
        """
        engine = HeatEngine()
        items = [mk("a", 1), mk("b", 50), mk("c", 50)]
        # a 未缺席，b/c 缺席；仅看 a 会得出上涨，但结论仍须是 unknown
        result = engine.compute(items, at=NOW, behind_sources={"b", "c"})
        assert result.trend == "unknown"


class TestDetectBehindSources:
    """源缺席的识别逻辑。"""

    def test_detects_sources_with_no_output(self):
        items = [mk("a", 1), mk("b", 2)]
        behind = detect_behind_sources(items, all_site_ids={"a", "b", "c"})
        assert behind == ["c"]

    def test_no_expected_list_means_no_judgement(self):
        """不传预期集合时不做判断 —— 无信息不等于「全部缺席」。"""
        items = [mk("a", 1)]
        assert detect_behind_sources(items, all_site_ids=None) == []

    def test_returned_list_is_sorted_and_deduplicated(self):
        items = [mk("a", 1)]
        behind = detect_behind_sources(items, all_site_ids={"z", "b", "c"})
        assert behind == sorted(behind)
        assert len(behind) == len(set(behind))


# ── 算法正确性 ────────────────────────────────────────────────────────


class TestHeatAlgorithm:

    def test_same_source_counts_once(self):
        """同一来源发多条只算一个参与者。"""
        engine = HeatEngine()
        items = [mk("a", 1), mk("a", 2), mk("a", 3), mk("b", 1)]
        result = engine.compute(items, at=NOW)
        assert result.participants == 2, "同源多条被重复计数了"

    def test_half_life_is_24_hours(self):
        """24 小时前的证据权重应恰为 0.5。"""
        engine = HeatEngine()
        h_now = engine.compute([mk("a", 0)], at=NOW).heat
        h_24 = engine.compute([mk("a", 24)], at=NOW).heat
        assert h_now == pytest.approx(1.0, abs=1e-6)
        assert h_24 == pytest.approx(0.5, abs=1e-6), "24 小时半衰期不对"

    def test_heat_decays_monotonically(self):
        """越旧的热度越低，且不可为负。"""
        engine = HeatEngine()
        heats = [engine.compute([mk("a", h)], at=NOW).heat for h in (1, 12, 24, 36, 47)]
        assert heats == sorted(heats, reverse=True), f"热度未单调衰减: {heats}"
        assert all(h > 0 for h in heats)

    def test_outside_window_is_excluded(self):
        """48 小时窗口外的证据不参与计算。"""
        engine = HeatEngine()
        result = engine.compute([mk("a", 49)], at=NOW)
        assert result.participants == 0
        assert result.heat == 0.0

    def test_unparsable_timestamp_is_dropped(self):
        """时间戳不可解析的记录不计入 —— 与归档裁剪同策略（不可证明即淘汰）。"""
        engine = HeatEngine()
        items = [mk("a", 1), {"site_id": "b", "published_at": "not-a-date"}]
        result = engine.compute(items, at=NOW)
        assert result.participants == 1, "坏时间戳的记录被计入了热度"

    def test_more_sources_means_higher_heat(self):
        """独立来源越多热度越高（同时间点）。"""
        engine = HeatEngine()
        one = engine.compute([mk("a", 1)], at=NOW).heat
        three = engine.compute([mk("a", 1), mk("b", 1), mk("c", 1)], at=NOW).heat
        assert three > one

    def test_future_timestamp_does_not_break(self):
        """未来时间戳不产生负衰减（不能出现权重 > 1）。"""
        engine = HeatEngine()
        result = engine.compute([mk("a", -5)], at=NOW)
        assert result.heat <= 1.0 + 1e-9

    def test_empty_input_is_safe(self):
        engine = HeatEngine()
        result = engine.compute([], at=NOW)
        assert result.participants == 0
        assert result.heat == 0.0
        assert result.trend == "unknown"


class TestParticipantKey:
    """参与键的优先级：owner > site_id > host。"""

    def test_owner_takes_priority(self):
        assert participant_key({"owner": "acme", "site_id": "s1"}) == "owner:acme"

    def test_falls_back_to_site_id(self):
        assert participant_key({"site_id": "s1"}) == "site:s1"

    def test_falls_back_to_host(self):
        key = participant_key({"url": "https://Example.COM/a/b"})
        assert key == "host:example.com"

    def test_unknown_when_nothing_available(self):
        assert participant_key({}) == "unknown"


class TestRankAndAnnotate:

    def test_single_source_group_is_filtered_out(self):
        """单来源不构成「事件热点」。"""
        engine = HeatEngine()
        groups = [[mk("a", 1)], [mk("b", 1), mk("c", 1)]]
        ranked = engine.rank(groups, at=NOW)
        assert len(ranked) == 1
        assert ranked[0].participants == 2

    def test_ranked_by_heat_descending(self):
        engine = HeatEngine()
        groups = [
            [mk("a", 30), mk("b", 30)],          # 旧，热度低
            [mk("c", 1), mk("d", 1), mk("e", 1)],  # 新，热度高
        ]
        ranked = engine.rank(groups, at=NOW)
        assert ranked[0].participants == 3, "热度排序不对"

    def test_annotate_returns_serialisable_dicts(self):
        engine = HeatEngine()
        payload = engine.annotate([[mk("a", 1), mk("b", 2)]], at=NOW)
        assert payload and isinstance(payload[0], dict)
        json.dumps(payload[0])  # 必须可序列化
        assert payload[0]["heat_rule"] == HEAT_RULE_VERSION
        assert "items" in payload[0]


# ── trend_detector 集成 ────────────────────────────────────────────────


class TestTrendDetectorIntegration:

    def _detector(self, tmp_path):
        from core.trend_engine.trend_detector import TrendDetector

        return TrendDetector(
            {
                "clustering": {"clustering_method": "tfidf"},
                "history_path": str(tmp_path / "trend_history.json"),
            }
        )

    def test_clusters_get_heat_fields(self, tmp_path):
        det = self._detector(tmp_path)
        arts = [
            mk("a", 1), mk("b", 2), mk("c", 3),
        ]
        for i, a in enumerate(arts):
            a["title"] = f"OpenAI GPT-6 release news {i}"
        out = det.analyze(arts, at=NOW, expected_site_ids={"a", "b", "c"})
        assert out["clusters"], "没有产出聚类"
        for c in out["clusters"]:
            assert "heat" in c
            assert "heat_trend" in c
            assert c["heat_rule"] == HEAT_RULE_VERSION

    def test_analyze_reports_behind_sources(self, tmp_path):
        det = self._detector(tmp_path)
        arts = [mk("a", 1), mk("b", 2)]
        for i, a in enumerate(arts):
            a["title"] = f"Semi news {i}"
        out = det.analyze(arts, at=NOW, expected_site_ids={"a", "b", "offline_src"})
        assert out["behind_sources"] == ["offline_src"]

    def test_hot_events_only_include_multi_source(self, tmp_path):
        """hot_events 只收多来源事件。"""
        det = self._detector(tmp_path)
        arts = [mk("a", 1), mk("b", 2)]
        for i, a in enumerate(arts):
            a["title"] = f"Shared topic story {i}"
        out = det.analyze(arts, at=NOW, expected_site_ids={"a", "b"})
        for ev in out["hot_events"]:
            assert int(ev["heat_participants"]) >= 2


# ── 对外出口 ──────────────────────────────────────────────────────────


class TestBuildFeeds:

    def _payload(self):
        return {
            "generated_at": NOW.isoformat(),
            "window_hours": 24,
            "total_items": 3,
            "archive_total": 10,
            "site_count": 2,
            "source_count": 3,
            "items_ai": [
                {
                    "title": "A & B <tag>",
                    "title_zh": "标题含 & 与 <尖括号>",
                    "url": "https://example.com/a?x=1&y=2",
                    "published_at": (NOW - timedelta(hours=1)).isoformat(),
                    "site_name": "site-a",
                    "signal_level": "S",
                    "signal_score": 92.0,
                    "tags": ["AI", "模型发布"],
                },
                {
                    "title": "B",
                    "url": "https://example.com/b",
                    "published_at": (NOW - timedelta(hours=30)).isoformat(),
                    "site_name": "site-b",
                    "signal_level": "C",
                    "signal_score": 40.0,
                },
            ],
            "hot_events": [
                {
                    "topic": "OpenAI 发布",
                    "heat": 12.3,
                    "heat_participants": 3,
                    "heat_trend": "up",
                }
            ],
        }

    def test_rss_is_well_formed_and_escaping_is_complete(self):
        """含 & 和 <> 的标题必须被正确转义，否则整个 feed 无法解析。"""
        from build_feeds import build_rss

        xml = build_rss(
            title="T & T",
            description="d < >",
            self_url="https://e.com/f.xml",
            site_url="https://e.com",
            items=self._payload()["items_ai"],
            generated_at=NOW,
        )
        root = ET.fromstring(xml)  # 不转义会在这里抛异常
        items = root.find("channel").findall("item")
        assert len(items) == 2
        assert items[0].findtext("title") == "标题含 & 与 <尖括号>"

    def test_rss_guid_is_permalink_url(self):
        """guid 用 url，重复抓取不产生新条目。"""
        from build_feeds import build_rss

        xml = build_rss(
            title="t", description="d", self_url="https://e.com/f.xml",
            site_url="https://e.com", items=self._payload()["items_ai"],
            generated_at=NOW,
        )
        root = ET.fromstring(xml)
        guids = [i.findtext("guid") for i in root.find("channel").findall("item")]
        links = [i.findtext("link") for i in root.find("channel").findall("item")]
        assert guids == links

    def test_daily_feed_excludes_old_and_low_signal(self):
        """日报只收近 24h 且信号 >= B 的内容。"""
        from build_feeds import _select_daily_items

        sel = _select_daily_items(self._payload()["items_ai"], NOW)
        urls = [i["url"] for i in sel]
        assert "https://example.com/a?x=1&y=2" in urls      # 近 24h + S 级
        assert "https://example.com/b" not in urls          # 30h 前且 C 级

    def test_hot_feed_prioritises_high_signal(self):
        from build_feeds import _select_hot_items

        sel = _select_hot_items(self._payload()["items_ai"], 10)
        assert sel[0]["signal_level"] == "S"

    def test_llms_txt_documents_the_unknown_trend_semantics(self):
        """llms.txt 必须写明 unknown 不等于下降 —— 否则 Agent 会误读。"""
        from build_feeds import build_llms_txt

        text = build_llms_txt(
            base_url="https://e.com", payload=self._payload(),
            generated_at=NOW, hot_events=self._payload()["hot_events"],
        )
        assert "unknown" in text
        assert "不要把它当作下降" in text or "不可信" in text
        assert "generated_at" in text

    def test_robots_welcomes_ai_crawlers(self):
        from build_feeds import build_robots

        txt = build_robots("https://e.com")
        assert "GPTBot" in txt
        assert "ClaudeBot" in txt
        assert "https://e.com/sitemap.xml" in txt

    def test_main_writes_all_artifacts(self, tmp_path):
        import build_feeds

        data = tmp_path / "latest.json"
        data.write_text(json.dumps(self._payload()), encoding="utf-8")
        old = sys.argv
        try:
            sys.argv = [
                "build_feeds", "--data", str(data),
                "--base-url", "https://e.com", "--out-dir", str(tmp_path),
            ]
            assert build_feeds.main() == 0
        finally:
            sys.argv = old

        for name in (
            "llms.txt", "robots.txt",
            "feed-hot.xml", "feed-all.xml", "feed-daily.xml",
        ):
            assert (tmp_path / name).exists(), f"缺少产物 {name}"


class TestFingerprintHandlesTextArtifacts:
    """对外出口的时间戳漂移必须被抑制，否则污染仓库。"""

    def test_rss_timestamp_drift_is_ignored(self):
        from snapshot_fingerprint import _canonical_text_hash

        a = "<rss><channel><lastBuildDate>Thu, 01 Oct 2026 15:31:06 +0000</lastBuildDate><item>x</item></channel></rss>"
        b = a.replace("15:31:06", "23:59:59")
        assert _canonical_text_hash(a) == _canonical_text_hash(b)

    def test_rss_content_change_is_detected(self):
        from snapshot_fingerprint import _canonical_text_hash

        a = "<rss><channel><lastBuildDate>X</lastBuildDate><item>x</item></channel></rss>"
        b = a.replace("<item>x</item>", "<item>y</item>")
        assert _canonical_text_hash(a) != _canonical_text_hash(b)

    def test_llms_timestamp_drift_is_ignored(self):
        from snapshot_fingerprint import _canonical_text_hash

        a = "# T\n最后更新：2026-10-01T15:31:06+00:00\n内容：1102 条\n"
        b = a.replace("2026-10-01T15:31:06", "2026-10-02T09:00:00")
        assert _canonical_text_hash(a) == _canonical_text_hash(b)

    def test_llms_content_change_is_detected(self):
        from snapshot_fingerprint import _canonical_text_hash

        a = "# T\n最后更新：X\n内容：1102 条\n"
        b = a.replace("1102", "1200")
        assert _canonical_text_hash(a) != _canonical_text_hash(b)

    def test_json_still_uses_json_path(self):
        from snapshot_fingerprint import _hash_for

        hashed, is_json = _hash_for("x.json", '{"generated_at":"a","k":1}')
        assert is_json is True
        assert hashed is not None

    def test_non_json_uses_text_path(self):
        from snapshot_fingerprint import _hash_for

        hashed, is_json = _hash_for("llms.txt", "hello")
        assert is_json is False
        assert hashed is not None


class TestWorkflowWiring:
    """确保对外出口真的被工作流生成并提交，而不是只存在于仓库里。"""

    @pytest.fixture(scope="class")
    def workflow(self) -> str:
        p = REPO_ROOT / ".github" / "workflows" / "update-news.yml"
        return p.read_text(encoding="utf-8")

    def test_workflow_generates_feeds(self, workflow: str):
        assert "build_feeds.py" in workflow, "工作流没有生成对外出口"

    def test_workflow_stages_feed_artifacts(self, workflow: str):
        for name in ("llms.txt", "feed-hot.xml", "feed-all.xml", "feed-daily.xml"):
            assert name in workflow, f"工作流未提交 {name}"

    def test_workflow_passes_feed_files_to_fingerprint(self, workflow: str):
        """必须把文本产物交给指纹脚本，否则每小时都会提交一次。"""
        assert re.search(
            r"snapshot_fingerprint\.py[\s\S]{0,400}?llms\.txt", workflow
        ), "指纹脚本未包含 llms.txt，时间戳漂移会绕过判定"

    def test_feed_step_does_not_abort_the_pipeline(self, workflow: str):
        """对外出口失败不应阻断数据更新。"""
        block = workflow.split("build_feeds.py", 1)[1][:300]
        assert "||" in block, "对外出口失败会中断主流程"

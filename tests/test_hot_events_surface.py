"""「热点」从生产到前端的可见性回归测试。

排查背景（2026-10-26）
----------------------
用户反馈「热点已经 2 天没在前端页面更新」。逐层核对后，**数据链路是健康的**：

* Stage 7 每轮都跑，``trends_detected: True``、``hot_events: 49``；
* ``origin/main`` 上的 ``latest-24h.json`` 是新鲜的；
* 部署校验 ``live site matches the published artifact``；
* Cloudflare 边缘缓存每轮都清。

问题出在**消费侧的三处断层**，本文件把它们逐一钉住：

1. ``hot_events`` 只有 ``topic`` / ``heat`` / ``heat_participants`` 这类聚合
   字段，**没有 url**。前端拿到的是一串不可点击的标题，所以这份数据一直
   在算、一直在写进快照，却从来没被页面消费 —— 页面上的「当前热点」只好
   退回「高分单篇」。
2. 前端「当前热点」读 ``a.hotness`` / ``a.stars``，而生产端写的是
   ``hotness_score`` / ``meta.stars_today``。两个分支永远不成立，排序**恒等
   退化**成 ``signal_score``：GitHub 热榜的 star 数被静默忽略，而 arXiv 每天
   一次性灌入近 200 篇同分（60.2 / 66.5）、同合成时间戳的论文，于是前 15 名
   里 13 席长期被标题高度同质化的论文占满 —— 看起来就像「热点一直没变」。
3. ``/trends`` 把「文件存在」当成「数据新鲜」，一直返回冻结在 2026-08-24
   的 ``trends.json``（该文件已无生产者）。

对应关系：第 1 条 → ``TestHotEventsCarryClickableRepresentative``；
第 2 条 → ``TestFrontendHotCardContract``；第 3 条 → ``TestTrendsEndpointFreshness``。
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.trend_engine.trend_detector import (  # noqa: E402
    TrendDetector,
    _pick_representative,
)

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
APP_JS = REPO_ROOT / "assets" / "app.js"


def _article(
    site: str,
    *,
    url: str | None = None,
    signal: float = 40.0,
    hours_ago: float = 1.0,
    title: str = "OpenAI launches GPT-6 reasoning model",
) -> dict:
    item = {
        "site_id": site,
        "site_name": site.upper(),
        "source": site.upper(),
        "title": title,
        "description": f"{title} — reported by {site}.",
        "signal_score": signal,
        "published_at": (NOW - timedelta(hours=hours_ago)).isoformat(),
    }
    if url is not None:
        item["url"] = url
    return item


def _merged_event() -> dict:
    """一条被去重合并过的内容：它自己 + 两家合并进来的来源。

    这是能同时激活 dedup 与 trend 两条路径的唯一形态，也是真实生产数据的
    常见形态（多家媒体同一事件 → 合并成一条，痕迹留在 merged_sources）。
    """
    item = _article("a", url="https://a.test/1")
    item["merged_sources"] = [
        {"site_id": "b", "site_name": "B", "source": "B", "url": "https://b.test/1"},
        {"site_id": "c", "site_name": "C", "source": "C", "url": "https://c.test/1"},
    ]
    return item


def _analyze(tmp_path: Path, articles: list[dict]) -> dict:
    det = TrendDetector({"history_path": str(tmp_path / "trend_history.json")})
    return det.analyze(articles, at=NOW)


# ── 1. hot_events 必须带可点击的代表条目 ────────────────────────────────


class TestHotEventsCarryClickableRepresentative:
    """没有 url 的「热点」渲染不出来，也就永远不会被页面消费。"""

    def test_event_carries_url_and_title(self, tmp_path):
        result = _analyze(tmp_path, [_merged_event()])
        events = result["hot_events"]
        assert len(events) == 1, events
        ev = events[0]
        assert ev["url"] == "https://a.test/1", "热点必须带可点击链接"
        assert ev["title"], "热点必须带标题"
        assert ev["site_name"], "热点必须带来源名，前端要显示它是谁在说"

    def test_event_keeps_aggregate_fields(self, tmp_path):
        """新增字段是**加法**，聚合语义不能被顺手改掉。"""
        ev = _analyze(tmp_path, [_merged_event()])["hot_events"][0]
        assert ev["heat_participants"] == 3
        assert ev["heat"] > 0
        assert set(ev["heat_sources"]) >= {"A", "B", "C"}
        assert ev["heat_trend"] in {"up", "down", "flat", "new", "unknown"}
        assert ev["size"] >= 1

    def test_representative_prefers_higher_signal_score(self):
        picked = _pick_representative(
            [
                _article("a", url="https://a.test/1", signal=10),
                _article("b", url="https://b.test/1", signal=80),
            ]
        )
        assert picked["url"] == "https://b.test/1"

    def test_representative_falls_back_to_newer_event_on_tie(self):
        picked = _pick_representative(
            [
                _article("a", url="https://a.test/old", signal=50, hours_ago=8),
                _article("b", url="https://b.test/new", signal=50, hours_ago=1),
            ]
        )
        assert picked["url"] == "https://b.test/new"

    def test_representative_skips_items_without_url(self):
        """没有链接的条目不能当代表 —— 否则前端会渲染出一个点不动的热点。"""
        picked = _pick_representative(
            [
                _article("a", url=None, signal=99),
                _article("b", url="https://b.test/1", signal=5),
            ]
        )
        assert picked["url"] == "https://b.test/1"

    def test_representative_prefers_items_that_also_have_a_title(self):
        """有 url 没标题的条目不能抢走代表位。

        否则前端只能退回显示簇话题，而话题是聚类生成的短语，可能和那条
        链接的实际内容对不上 —— 渲染出一条「标题与链接不符」的热点。
        """
        no_title = _article("a", url="https://a.test/1", signal=99)
        no_title.pop("title")
        no_title["description"] = "still clusterable"
        titled = _article("b", url="https://b.test/1", signal=5)

        picked = _pick_representative([no_title, titled])
        assert picked["url"] == "https://b.test/1"

    def test_representative_falls_back_to_untitled_when_that_is_all_there_is(self):
        """只有无标题条目时仍然要给出链接 —— 缺标题是降级，不是丢数据。"""
        no_title = _article("a", url="https://a.test/1", signal=99)
        no_title.pop("title")
        assert _pick_representative([no_title])["url"] == "https://a.test/1"

    def test_representative_is_empty_when_no_item_has_url(self):
        assert _pick_representative([_article("a", url=None, signal=99)]) == {}

    def test_event_without_any_url_is_still_listed(self, tmp_path):
        """没有 url 也要保留事件本身 —— 缺链接是**降级**，不是**丢数据**。

        前端拿到空 url 会渲染成不可点的行；但事件的热度、参与来源数
        仍然是有价值的信息，不该因此整条消失。
        """
        item = _article("a", url=None)
        item["merged_sources"] = [
            {"site_id": "b", "site_name": "B", "source": "B"},
        ]
        events = _analyze(tmp_path, [item])["hot_events"]
        assert len(events) == 1
        assert events[0]["url"] == ""
        assert events[0]["heat_participants"] == 2


# ── 2. 前端「当前热点」的消费契约 ──────────────────────────────────────


class TestFrontendHotCardContract:
    """前端与生产端的字段契约。这类漂移不会报错，只会静默降级。

    用文本断言而不是跑浏览器：要防的是**字段名/优先级**这种声明式契约，
    读源码最直接，也最不容易因为 DOM 细节变化而误报。
    """

    @pytest.fixture(scope="class")
    def js(self) -> str:
        return APP_JS.read_text(encoding="utf-8")

    def test_frontend_reads_hot_events(self, js):
        assert "payload.hot_events" in js, "「当前热点」必须优先消费事件级热度"

    def test_frontend_has_article_fallback(self, js):
        """hot_events 为空时不能白屏 —— 必须能回退到高分单篇。"""
        assert "articleRows" in js
        assert 'hotMode = "articles"' in js

    def test_frontend_reads_producer_field_names(self, js):
        """读的必须是生产端真正写的字段名。"""
        assert "hotness_score" in js, "生产端写的是 hotness_score，不是 hotness"
        assert "stars_today" in js or "total_stars" in js, (
            "GitHub 热榜的 star 数在 meta.stars_today / meta.total_stars 里"
        )

    def test_frontend_does_not_read_nonexistent_fields(self, js):
        """反证：`a.hotness` / `a.stars` 在 payload 里根本不存在。

        这两个分支曾经永远不成立，导致排序恒等退化成 signal_score，
        而 GitHub 热榜的 star 数被静默忽略。
        """
        assert not re.search(r"\.hotness\b", js), "payload 里没有 hotness 字段"
        assert not re.search(r"\.stars\b", js), "payload 里没有 stars 字段"

    def test_frontend_caps_single_source_in_fallback(self, js):
        """回退路径必须有来源多样性约束。

        否则单源批量灌入（如 arXiv 每天近 200 篇）会再次挤满整个榜单，
        把「热点没更新」原样重演一遍。
        """
        assert "PER_SITE_CAP" in js
        assert re.search(r"PER_SITE_CAP\s*=\s*\d+", js), "上限必须是可读的常量"

    def test_frontend_does_not_invent_arrows_for_unknown_trend(self, js):
        """``unknown`` 是「证据不完整、方向不可判定」，不是「下降」。

        用视觉符号把「没抓到」说成「变冷了」，正是本项目反复踩过的
        「不可观测 ⇒ 不可断言」那类错误。
        """
        assert 'trend === "unknown"' not in js or "arrow" in js
        # 箭头映射里不能出现 unknown -> ▼
        assert not re.search(r'unknown"?\s*\?\s*"[^"]*[▼↓]', js)


# ── 4. llms.txt 的热度榜要真的可读 ──────────────────────────────────────


class TestLlmsHotListUsesTheRepresentative:
    """``topic`` 是聚类短语（「开源」「模型发布」），单看没有信息量。

    有了代表条目之后，给 LLM 的输出应当变成「哪件事 + 去哪读原文」。
    """

    def _llms(self, hot_events):
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        from build_feeds import build_llms_txt

        return build_llms_txt(
            base_url="https://e.com",
            payload={"generated_at": NOW.isoformat(), "items_ai": []},
            generated_at=NOW,
            hot_events=hot_events,
        )

    def test_hot_list_shows_title_and_link(self):
        text = self._llms(
            [
                {
                    "topic": "开源",
                    "heat": 24.1,
                    "heat_participants": 3,
                    "heat_trend": "up",
                    "title_zh": "Reddit r/opensource 封杀 AI 生成内容",
                    "url": "https://a.test/1",
                }
            ]
        )
        assert "Reddit r/opensource 封杀 AI 生成内容" in text
        assert "https://a.test/1" in text

    def test_hot_list_degrades_to_topic_without_representative(self):
        """没有代表条目时退回只显示话题 —— 不能因此报错或输出空行。"""
        text = self._llms(
            [{"topic": "模型发布", "heat": 9.0, "heat_participants": 2,
              "heat_trend": "unknown"}]
        )
        assert "模型发布" in text
        assert "趋势 unknown" in text


# ── 3. /trends 不能把「文件存在」当成「数据新鲜」 ───────────────────────


class TestTrendsEndpointFreshness:
    """``data/trends.json`` 已无生产者，冻结在 2026-08-24。

    旧代码只要发现它有 ``trends`` 就直接返回，于是这个接口对外提供了
    两个月前的趋势数据 —— 用户看到的「热点不更新」正是这一类。
    """

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        import api.app as app_module

        app_module._CACHE.clear()
        yield
        app_module._CACHE.clear()

    def _write(self, path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _client(self, tmp_path: Path, monkeypatch):
        from fastapi.testclient import TestClient

        import api.app as app_module

        monkeypatch.setattr(app_module, "DATA_DIR", tmp_path)
        return TestClient(app_module.app)

    def test_trends_prefers_hot_events(self, tmp_path, monkeypatch):
        """有 hot_events 时，即便 trends.json 新鲜也不该抢在前面。"""
        self._write(
            tmp_path / "latest-24h.json",
            {
                "generated_at": NOW.isoformat(),
                "items_ai": [],
                "hot_events": [
                    {
                        "topic": "GPT-6",
                        "heat": 42.0,
                        "heat_trend": "up",
                        "heat_participants": 4,
                        "url": "https://a.test/1",
                        "title": "GPT-6 launched",
                    }
                ],
                "heat_rule": "heat-v1-48h-halflife24h",
            },
        )
        self._write(
            tmp_path / "trends.json",
            {"generated_at": NOW.isoformat(), "trends": [{"tag": "old"}]},
        )

        body = self._client(tmp_path, monkeypatch).get("/trends").json()
        assert body["source"] == "hot_events"
        assert body["trends"][0]["url"] == "https://a.test/1"
        assert body["heat_rule"] == "heat-v1-48h-halflife24h"

    def test_stale_trends_json_is_ignored(self, tmp_path, monkeypatch):
        """冻结两个月的 trends.json 必须被忽略，回退到 tag 计数。"""
        self._write(
            tmp_path / "latest-24h.json",
            {
                "generated_at": NOW.isoformat(),
                "items_ai": [
                    {"title": "x", "url": "https://x.test/1", "tags": ["模型发布"]}
                ],
                "hot_events": [],
            },
        )
        self._write(
            tmp_path / "trends.json",
            {
                "generated_at": "2026-08-24T03:43:46.984067Z",
                "trends": [{"tag": "两个月前的旧趋势"}],
            },
        )

        body = self._client(tmp_path, monkeypatch).get("/trends").json()
        assert body["source"] == "tag_fallback", (
            "陈旧文件不能因为「存在」就被当成当前趋势"
        )
        assert all(t.get("tag") != "两个月前的旧趋势" for t in body["trends"])

    def test_fresh_trends_json_is_still_used(self, tmp_path, monkeypatch):
        """新鲜时仍然采信旧产物 —— 修复不能把可用路径一起砍掉。"""
        self._write(
            tmp_path / "latest-24h.json",
            {"generated_at": NOW.isoformat(), "items_ai": [], "hot_events": []},
        )
        self._write(
            tmp_path / "trends.json",
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "trends": [{"tag": "新趋势"}],
            },
        )

        body = self._client(tmp_path, monkeypatch).get("/trends").json()
        assert body["source"] == "trend_engine"
        assert body["trends"][0]["tag"] == "新趋势"

    def test_trends_reports_data_age(self, tmp_path, monkeypatch):
        """响应必须自带数据年龄。

        只给 ``generated_at`` 而不给 ``age``，等于把「换算 + 判断」都推给
        消费方，而多数消费方不会做 —— 于是陈旧数据会被当成当前数据用。
        """
        self._write(
            tmp_path / "latest-24h.json",
            {
                "generated_at": (
                    datetime.now(timezone.utc) - timedelta(hours=30)
                ).isoformat(),
                "items_ai": [],
                "hot_events": [{"topic": "t", "heat": 1.0, "heat_participants": 2}],
            },
        )
        body = self._client(tmp_path, monkeypatch).get("/trends").json()
        assert body["source"] == "hot_events"
        assert body["data_age_hours"] >= 29
        assert body["stale"] is True, "30 小时前的数据必须被标成 stale"

    def test_missing_everything_is_503(self, tmp_path, monkeypatch):
        resp = self._client(tmp_path, monkeypatch).get("/trends")
        assert resp.status_code == 503

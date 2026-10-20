"""Index-Translate（B站免费翻译 API）接入的回归测试。

背景：2026-10-20 把标题翻译的**首选**后端换成 B站 Index LLM 团队开源的
Index-Translate-35B-A3B（官方 OpenAI 兼容免费公共服务，
https://index-translate.bilibili.com/v1 ，无需 API Key）。

选它的理由是实测，不是宣传：GitHub runner 可达（TLS 0.108s，连发 8 次全 200）、
批量 8 条 2.8-13s（OpenRouter 免费模型约 17s）、专门的翻译模型对术语/格式
保留更好。而 OpenRouter 免费额度是**平台级 50 次/天**，多账号组池不能乘以
账号数 —— 那是当前 AI 翻译的实际天花板。

但它是**第三方公共服务、没有任何 SLA**，所以本文件把这些行为逐条钉住：

  1. 请求形状：不带 Authorization（本来就不需要 key）、必须带
     `chat_template_kwargs.enable_thinking=false`（否则思维链文本会混进译文）、
     User-Agent 用官方示例值、URL/模型名与官方一致。
  2. 三级链路：Index-Translate（首选）→ OpenRouter 池 → Google 兜底。
     任何一级失败都必须把标题交给下一级，**一条都不能丢**。
  3. 时间上界：三级共用同一个墙上时钟，Index 先划走子预算，OpenRouter 只剩
     剩下的。否则各级都不超、加起来能把 job 拖到超时 kill
     （run 36890199782 的 39 分钟就是这么来的）。
  4. 中止语义：预算耗尽 / 连续失败时，**尚未开始的标题也必须交出去** ——
     这是实现过程中真实修掉的 bug（break 时未处理的批次会静默消失）。

全部用例不联网（tests/conftest.py 有 URL 层守卫），并对
`INDEX_TRANSLATE_ENABLED` 显式控制：既有的 OpenRouter 路由池断言不受影响。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from core.normalize import translator as T
from core.normalize.translator import (
    _ai_translate_batch_index,
    _index_translate_post,
    _run_index_translate_phase,
    add_bilingual_fields,
    reset_circuit_breaker,
    reset_google_circuit_breaker,
)

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------


def _ok(content: str) -> MagicMock:
    """HTTP 200 的 Index-Translate 响应（结构与 OpenRouter 侧一致）。"""
    resp = MagicMock()
    resp.status_code = 200
    resp.text = "ok"
    resp.json.return_value = {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
    return resp


def _err(status: int, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = text or f"HTTP {status}"
    resp.json.return_value = {"error": {"message": resp.text}}
    resp.headers = {}
    return resp


def _index_session(responses: list) -> MagicMock:
    """按脚本顺序吐响应的假 session（脚本用尽即断言失败）。"""
    queue = list(responses)

    def _next(*_args, **_kwargs):
        if not queue:
            raise AssertionError("mock session 被调用次数超过了脚本中的响应数量")
        return queue.pop(0)

    s = MagicMock(spec=requests.Session)
    s.post.side_effect = _next
    return s


def _call_post(session_responses: list, **kwargs) -> tuple[str | None, dict]:
    """执行一次 `_index_translate_post`，返回 (content, outcome)。"""
    session = _index_session(session_responses)
    outcome: dict = {}
    with patch.object(T, "_index_translate_session", return_value=session):
        content = _index_translate_post(
            MagicMock(spec=requests.Session),
            system_prompt=kwargs.get("system_prompt", "SYS"),
            user_text=kwargs.get("user_text", "1. Hello"),
            max_tokens=kwargs.get("max_tokens", 512),
            outcome=outcome,
        )
    return content, outcome


def _items(count: int) -> list[dict[str, object]]:
    return [
        {"title": f"English Title {i}", "url": f"https://example.com/{i}"}
        for i in range(count)
    ]


def _batch_reply(count: int) -> str:
    return "\n".join(f"{i + 1}. 译文{i + 1}" for i in range(count))


def _phase_pending(count: int) -> list[tuple[dict[str, object], str]]:
    return [({"title": f"Title {i}", "url": f"https://e.com/{i}"}, f"Title {i}") for i in range(count)]


def _run_phase(
    pending: list,
    scripted: list | None = None,
    *,
    batch_size: int = 2,
    max_tries: int = 1,
    max_consecutive: int = 3,
    budget: float = 60,
) -> tuple[list, int, list]:
    """跑一次 `_run_index_translate_phase`，返回 (剩余条目, 成功数, 每批收到的标题)。"""
    scripted = scripted if scripted is not None else [None]
    calls: list[list[str]] = []

    def fake(_session, titles, **_kwargs):
        calls.append(list(titles))
        i = len(calls) - 1
        result = scripted[i] if i < len(scripted) else scripted[-1]
        if not isinstance(result, list):
            result = [None] * len(titles)
        return result

    cache: dict[str, str] = {}
    with patch.object(T, "_ai_translate_batch_index", side_effect=fake), \
            patch.object(T, "BATCH_SIZE", batch_size), \
            patch.object(T, "INDEX_TRANSLATE_MAX_TRIES", max_tries), \
            patch.object(T, "INDEX_TRANSLATE_MAX_CONSECUTIVE_FAILURES", max_consecutive), \
            patch.object(T, "INDEX_TRANSLATE_BUDGET_SECONDS", budget), \
            patch.object(T, "INDEX_TRANSLATE_ENABLED", True):
        remaining, done = _run_index_translate_phase(
            MagicMock(), pending, cache, budget_seconds=budget
        )
    return remaining, done, calls


@pytest.fixture(autouse=True)
def _index_enabled_by_default():
    """本文件默认启用 Index-Translate；禁用路径在用例内显式覆盖为 False。"""
    with patch.object(T, "INDEX_TRANSLATE_ENABLED", True):
        yield


@pytest.fixture(autouse=True)
def _no_sleep():
    """Index 阶段与 OpenRouter 阶段之间都有礼貌性 sleep，测试里打成 no-op。"""
    with patch.object(T.time, "sleep", lambda *_a, **_k: None):
        yield


# ---------------------------------------------------------------------------
# 1. 请求形状：必须与官方文档一致
# ---------------------------------------------------------------------------


class RequestShapeTests(unittest.TestCase):
    def setUp(self):
        reset_circuit_breaker()
        reset_google_circuit_breaker()

    def tearDown(self):
        reset_circuit_breaker()
        reset_google_circuit_breaker()

    def test_url_is_the_official_endpoint(self):
        assert T.INDEX_TRANSLATE_API_URL == "https://index-translate.bilibili.com/v1/chat/completions"

    def test_model_name_has_no_preview_suffix(self):
        """API 侧模型名是 Index-Translate-35B-A3B，HuggingFace 权重才带 -preview。

        写错后缀在上游表现为 404，而 404 在 OpenRouter 侧意味着「淘汰模型」——
        一个纯笔误会把整条链路判死，所以必须钉住。
        """
        assert T.INDEX_TRANSLATE_MODEL == "Index-Translate-35B-A3B"
        assert "preview" not in T.INDEX_TRANSLATE_MODEL

    def test_payload_shape(self):
        session = _index_session([_ok("好的")])
        with patch.object(T, "_index_translate_session", return_value=session):
            _index_translate_post(
                MagicMock(spec=requests.Session),
                system_prompt="SYS", user_text="1. Hello", max_tokens=1024, outcome={},
            )
        args, kwargs = session.post.call_args
        assert args[0] == T.INDEX_TRANSLATE_API_URL
        body = kwargs["json"]
        assert body["model"] == T.INDEX_TRANSLATE_MODEL
        assert body["temperature"] == 0.0
        assert body["max_tokens"] == 1024
        assert body["messages"][0] == {"role": "system", "content": "SYS"}
        assert body["messages"][1] == {"role": "user", "content": "1. Hello"}

    def test_thinking_is_disabled_in_the_payload(self):
        """必须关思维链：该模型模板默认会输出思考过程，CoT 文本会混进译文。

        官方示例与本地代理都硬编码了这一项，不是可选项。
        """
        session = _index_session([_ok("好的")])
        with patch.object(T, "_index_translate_session", return_value=session):
            _index_translate_post(
                MagicMock(spec=requests.Session),
                system_prompt="SYS", user_text="1. Hello", max_tokens=64, outcome={},
            )
        body = session.post.call_args.kwargs["json"]
        assert body["chat_template_kwargs"] == {"enable_thinking": False}

    def test_no_authorization_header_is_sent(self):
        """这个后端不需要 key —— 发 Authorization 反而多暴露一个凭据。"""
        session = _index_session([_ok("好的")])
        with patch.object(T, "_index_translate_session", return_value=session):
            _index_translate_post(
                MagicMock(spec=requests.Session),
                system_prompt="SYS", user_text="1. Hello", max_tokens=64, outcome={},
            )
        headers = session.post.call_args.kwargs["headers"]
        assert "Authorization" not in headers
        assert headers["User-Agent"] == T.INDEX_TRANSLATE_USER_AGENT

    def test_session_disables_retries(self):
        """重试由上层负责，HTTP 层自己重试会把 429/5xx 吞成黑盒。"""
        parent = MagicMock(spec=requests.Session)
        parent.headers = {}
        s = T._index_translate_session(parent)
        adapter = s.get_adapter(T.INDEX_TRANSLATE_API_URL)
        self.assertEqual(adapter.max_retries.total, 0)

    def test_prompt_forbids_transliteration(self):
        """专用提示词必须把「不要音译」写成显式约束。

        实测：复用 OpenRouter 的 `AI_BATCH_TRANSLATE_SYSTEM_PROMPT` 时
        "Anthropic" 被音译成「安赛克」—— 那条提示词只举例了
        GPT-5/Claude/OpenAI，模型于是认为没点名的公司可以音译。
        """
        prompt = T.INDEX_TRANSLATE_BATCH_SYSTEM_PROMPT
        assert "不要音译" in prompt
        assert "编号" in prompt
        assert "Anthropic" in prompt

    def test_batch_max_tokens_scales_with_batch(self):
        session = _index_session([_ok("1. 甲")])
        with patch.object(T, "_index_translate_session", return_value=session):
            _ai_translate_batch_index(MagicMock(), ["A", "B", "C", "D"], outcome={})
        assert session.post.call_args.kwargs["json"]["max_tokens"] == max(150 * 4, 512)


# ---------------------------------------------------------------------------
# 2. 失败归因：这个后端没有 key 维度可淘汰
# ---------------------------------------------------------------------------


class OutcomeAttributionTests(unittest.TestCase):
    def setUp(self):
        reset_circuit_breaker()
        reset_google_circuit_breaker()

    def tearDown(self):
        reset_circuit_breaker()
        reset_google_circuit_breaker()

    def test_200_returns_content_and_records_ok(self):
        content, outcome = _call_post([_ok("英伟达发布新架构")])
        self.assertEqual(content, "英伟达发布新架构")
        self.assertEqual(outcome["status"], "ok")

    def test_429_is_rate_limited_not_key_exhausted(self):
        """429 只能记成「被限流」，绝不能记成 key_exhausted。

        这个后端**没有 key 维度**，若沿用 OpenRouter 的归因，上层会去
        淘汰一个不存在的 key —— 那是把「没有的概念」当成已知。
        """
        content, outcome = _call_post([_err(429, "too many requests")])
        self.assertIsNone(content)
        self.assertEqual(outcome["status"], "rate_limited")
        self.assertNotEqual(outcome["status"], "key_exhausted")

    def test_network_error_is_recorded(self):
        session = MagicMock(spec=requests.Session)
        session.post.side_effect = requests.ConnectionError("boom")
        with patch.object(T, "_index_translate_session", return_value=session):
            content = _index_translate_post(
                MagicMock(), system_prompt="S", user_text="u", max_tokens=64,
                outcome=(outcome := {}),
            )
        self.assertIsNone(content)
        self.assertEqual(outcome["status"], "network_error")

    def test_unexpected_exception_does_not_escape(self):
        """测试期 URL 层守卫抛的是 RuntimeError，不是 RequestException。

        兜底必须捕获所有异常类型，否则一个守卫异常能把整轮翻译掀翻。
        """
        session = MagicMock(spec=requests.Session)
        session.post.side_effect = RuntimeError("测试中检测到真实外网请求")
        with patch.object(T, "_index_translate_session", return_value=session):
            content = _index_translate_post(
                MagicMock(), system_prompt="S", user_text="u", max_tokens=64,
                outcome=(outcome := {}),
            )
        self.assertIsNone(content)
        self.assertIn("error", outcome)

    def test_server_error_records_status_and_body(self):
        """只记「非 200」等于没有归因 —— body 里才有直接原因。"""
        content, outcome = _call_post([_err(504, "Gateway Time-out")])
        self.assertIsNone(content)
        self.assertEqual(outcome["status"], "server_error")
        self.assertEqual(outcome["http"], 504)
        self.assertIn("Gateway", outcome["body"])

    def test_empty_content_is_recorded(self):
        content, outcome = _call_post([_ok("")])
        self.assertIsNone(content)
        self.assertEqual(outcome["status"], "empty")


# ---------------------------------------------------------------------------
# 3. 阶段逻辑：预算 / 重试 / 连续失败 / 剩余条目不能被丢
# ---------------------------------------------------------------------------


class PhaseLogicTests(unittest.TestCase):
    def setUp(self):
        reset_circuit_breaker()
        reset_google_circuit_breaker()

    def tearDown(self):
        reset_circuit_breaker()
        reset_google_circuit_breaker()

    def test_all_hit_fills_cache_and_leaves_nothing(self):
        remaining, done, calls = _run_phase(
            _phase_pending(4), [["甲", "乙"], ["丙", "丁"]], batch_size=2
        )
        self.assertEqual(done, 4)
        self.assertEqual(remaining, [])
        self.assertEqual(len(calls), 2)

    def test_partial_hit_keeps_hits_and_defers_rest(self):
        """部分命中：拿到的落缓存，没拿到的交给下一级 —— 两件事都要做。"""
        cache: dict[str, str] = {}
        pending = _phase_pending(4)
        with patch.object(T, "_ai_translate_batch_index", side_effect=lambda *a, **k: ["甲", None]), \
                patch.object(T, "BATCH_SIZE", 2), \
                patch.object(T, "INDEX_TRANSLATE_MAX_TRIES", 1), \
                patch.object(T, "INDEX_TRANSLATE_MAX_CONSECUTIVE_FAILURES", 9), \
                patch.object(T, "INDEX_TRANSLATE_BUDGET_SECONDS", 60):
            remaining, done = _run_index_translate_phase(MagicMock(), pending, cache)
        self.assertEqual(done, 2)
        self.assertEqual(len(remaining), 2, "没译出的 2 条必须交给下一级")
        self.assertEqual(len(cache), 2, "已译出的 2 条必须落缓存，不能退回重译")

    def test_batch_is_retried_within_cap(self):
        """单批全军覆没要重试 —— 服务端偶发 504，重试常能救回整批 8 条。"""
        remaining, done, calls = _run_phase(
            _phase_pending(2), [[None, None], ["甲", "乙"]], batch_size=2, max_tries=2
        )
        self.assertEqual(done, 2)
        self.assertEqual(remaining, [])
        self.assertEqual(len(calls), 2, "第一次全军覆没 → 重试一次即成功")

    def test_batch_is_not_retried_beyond_cap(self):
        remaining, done, calls = _run_phase(
            _phase_pending(2), scripted=[[None, None]], batch_size=2, max_tries=2
        )
        self.assertEqual(done, 0)
        self.assertEqual(len(calls), 2, "上限是 2 次，不该把时间烧在注定失败的批上")
        self.assertEqual(len(remaining), 2)

    def test_consecutive_failures_abort_the_stage(self):
        """连续 N 批全军覆没 → 放弃 Index，剩余交下一级。

        它是无 SLA 的第三方服务，死磕等于把预算烧在注定失败的上游上。
        """
        remaining, done, calls = _run_phase(
            _phase_pending(6), scripted=[[None] * 2], batch_size=2, max_tries=1, max_consecutive=2
        )
        self.assertEqual(done, 0)
        self.assertEqual(len(calls), 2, "连续 2 批失败即中止，不该把 3 批全烧掉")
        self.assertEqual(len(remaining), 6, "剩余标题一条都不能丢")

    def test_abort_keeps_titles_that_were_never_started(self):
        """⚠️ 核心回归：中止时**尚未开始**的标题也必须交出去。

        实现时 break 发生在 `batch = pending[index:]` 之前，未处理的批次
        天然不在 remaining 里 —— 曾真实导致这些标题被静默丢弃（缓存里没有，
        Google 也可能不可达，等于永久丢失）。
        """
        remaining, done, calls = _run_phase(
            _phase_pending(4), scripted=[[None]], batch_size=1, max_tries=1, max_consecutive=2
        )
        self.assertEqual(done, 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            len(remaining), 4,
            "中止时只处理了 2 批，未开始的 2 批必须一并转交",
        )

    def test_zero_budget_aborts_without_any_request(self):
        remaining, done, calls = _run_phase(_phase_pending(4), scripted=[["甲"]], budget=0)
        self.assertEqual(done, 0)
        self.assertEqual(calls, [], "预算为 0 时一条请求都不该发")
        self.assertEqual(len(remaining), 4)

    def test_hits_before_abort_are_kept(self):
        """阶段中途中止：已译出的标题必须留在缓存，未开始的必须交出去。

        场景：8 条 / 每批 2 条 = 4 批；第 1 批成功 2 条，第 2、3 批连续失败
        触发中止（max_consecutive=2），第 4 批根本没开始。
        期望：done=2（前面的成功不白干）、remaining=6（2+2 失败 + 2 未开始）。
        """
        pending = _phase_pending(8)
        scripted = [["甲1", "甲2"], [None, None], [None, None], ["不该被调用"]]
        cache: dict[str, str] = {}
        calls: list[int] = []

        def fake(_session, titles, **_kwargs):
            calls.append(1)
            i = len(calls) - 1
            return scripted[i]

        with patch.object(T, "_ai_translate_batch_index", side_effect=fake), \
                patch.object(T, "BATCH_SIZE", 2), \
                patch.object(T, "INDEX_TRANSLATE_MAX_TRIES", 1), \
                patch.object(T, "INDEX_TRANSLATE_MAX_CONSECUTIVE_FAILURES", 2), \
                patch.object(T, "INDEX_TRANSLATE_BUDGET_SECONDS", 60):
            remaining, done = _run_index_translate_phase(MagicMock(), pending, cache)

        self.assertEqual(done, 2, "中止前成功的 2 条不能作废")
        self.assertEqual(len(cache), 2)
        self.assertEqual(len(calls), 3, "第 3 批失败后即中止，第 4 批不该被请求")
        self.assertEqual(
            len(remaining), 6,
            "2 条已失败 + 2 条刚失败 + 2 条未开始 = 6 条都要交给下一级",
        )

    def test_budget_total_is_capped_by_caller_supplied_value(self):
        """调用方传入的预算必须真的生效 —— 读模块常量会让两级账不平。"""
        # 传入 0 等价于「这一级一点时间都没有」
        remaining, done, calls = _run_phase(
            _phase_pending(4), scripted=[["甲", "乙"]], batch_size=2, budget=0
        )
        self.assertEqual(done, 0)
        self.assertEqual(calls, [])
        self.assertEqual(len(remaining), 4)


# ---------------------------------------------------------------------------
# 4. 三级链路：Index → OpenRouter → Google，一条都不能丢
# ---------------------------------------------------------------------------


class ThreeStageChainTests(unittest.TestCase):
    def setUp(self):
        reset_circuit_breaker()
        reset_google_circuit_breaker()

    def tearDown(self):
        reset_circuit_breaker()
        reset_google_circuit_breaker()

    def _run_chain(self, *, index_results, or_responses, keys="k1", models="m1",
                   index_enabled=True):
        or_session = _index_session(or_responses)
        cache: dict[str, str] = {}
        with patch.object(T, "_openrouter_session", return_value=or_session), \
                patch.object(T, "_ai_translate_batch_index", side_effect=index_results), \
                patch.object(T, "_google_session") as mk_google, \
                patch.object(T, "INDEX_TRANSLATE_ENABLED", index_enabled), \
                patch.object(T, "BATCH_SIZE", 8), \
                patch.dict("os.environ", {"OPENROUTER_KEYS": keys, "OPENROUTER_MODELS": models}):
            mk_google.return_value.get.side_effect = requests.RequestException("google down")
            ai_out, _all, cache = add_bilingual_fields(
                _items(8), _items(8), MagicMock(spec=requests.Session), cache,
                max_new_translations=8,
            )
        return cache, or_session, ai_out

    def test_index_wins_and_openrouter_is_untouched(self):
        """Index 全部成功时，OpenRouter 一次都不该被调用。

        这是「Index 真的排在前面」的证明 —— 若顺序写反，这个断言会失败。
        """
        cache, or_session, ai_out = self._run_chain(
            index_results=lambda *_a, **_k: ["译文%d" % i for i in range(1, 9)],
            or_responses=[],  # 若 OpenRouter 被调用，脚本为空会直接 AssertionError
        )
        self.assertEqual(len(cache), 8)
        or_session.post.assert_not_called()
        for item in ai_out:
            self.assertIn(" / ", item.get("title_bilingual", ""))

    def test_index_miss_hands_over_to_openrouter(self):
        """Index 全军覆没 → OpenRouter 必须接手把活干完，标题不能被丢。"""
        cache, or_session, _ai = self._run_chain(
            index_results=lambda *_a, **_k: [None] * 8,
            or_responses=[_ok(_batch_reply(8))],
        )
        self.assertEqual(len(cache), 8, "Index 全失败时 OpenRouter 必须接手")
        self.assertGreaterEqual(or_session.post.call_count, 1)

    def test_index_partial_hit_lets_openrouter_finish(self):
        batch = ["甲1", "甲2", "甲3", "甲4", None, None, None, None]
        cache, or_session, _ai = self._run_chain(
            index_results=lambda *_a, **_k: list(batch),
            or_responses=[_ok(_batch_reply(8))],
        )
        self.assertEqual(len(cache), 8, "两级加起来必须把这 8 条全部解决")

    def test_index_disabled_keeps_openrouter_only_path(self):
        """禁用开关必须真的关掉，不影响原有路径。"""
        cache, or_session, _ai = self._run_chain(
            index_results=lambda *_a, **_k: pytest.fail("禁用时不该调用 Index-Translate"),
            or_responses=[_ok(_batch_reply(8))],
            index_enabled=False,
        )
        self.assertEqual(len(cache), 8)

    def test_index_exception_degrades_to_openrouter(self):
        """Index 阶段异常只能等价于「它没干活」，绝不能掀翻整轮翻译。"""
        def boom(*_a, **_k):
            raise RuntimeError("unexpected")

        cache, or_session, _ai = self._run_chain(
            index_results=boom, or_responses=[_ok(_batch_reply(8))],
        )
        self.assertEqual(len(cache), 8, "Index 抛异常时 OpenRouter 仍要能译完全部")

    def test_openrouter_receives_remaining_budget(self):
        """三级共用一个墙上时钟：Index 划走子预算，OpenRouter 只剩剩下的。

        否则 worst_case = Index 批次数×重试 + OpenRouter 路由数×超时，
        两部分各自都不超，加起来足以把 job 拖到超时 kill。
        """
        captured: dict[str, float] = {}

        class _RecordingCursor(T._RouteCursor):
            def __init__(self, keys, models, budget_seconds):
                captured["budget"] = budget_seconds
                super().__init__(keys, models, budget_seconds)

        with patch.object(T, "_RouteCursor", _RecordingCursor), \
                patch.object(T, "AI_TRANSLATE_BUDGET_SECONDS", 100.0), \
                patch.object(T, "INDEX_TRANSLATE_BUDGET_SECONDS", 30.0), \
                patch.object(T, "_openrouter_session", return_value=_index_session([_err(503)] * 4)), \
                patch.object(T, "_ai_translate_batch_index", side_effect=lambda *_a, **_k: [None] * 8), \
                patch.object(T, "_google_session") as mk_google, \
                patch.object(T, "INDEX_TRANSLATE_ENABLED", True), \
                patch.object(T, "BATCH_SIZE", 8), \
                patch.dict("os.environ", {"OPENROUTER_KEYS": "k1", "OPENROUTER_MODELS": "m1"}):
            mk_google.return_value.get.side_effect = requests.RequestException("google down")
            add_bilingual_fields(
                _items(8), _items(8), MagicMock(spec=requests.Session), {}, max_new_translations=8
            )
        self.assertEqual(captured["budget"], 70.0, "OpenRouter 预算应是 100 - 30")


if __name__ == "__main__":
    unittest.main()

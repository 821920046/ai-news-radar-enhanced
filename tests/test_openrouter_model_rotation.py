"""OpenRouter 多模型 × 多账号 key 轮询的回归测试。

背景（实测 run 36960192663）：项目配了多个账号的 OpenRouter key，但每轮
「AI 翻译 0 条，全部降级 Google Translate」。日志里只有一行
`Max retries exceeded ... too many 429 error responses` 和一行
`[AI 熔断器] 连续 3 次 AI 翻译失败，触发全局熔断！`。

四个独立缺陷叠加：
  1. 熔断语义错误：`_mark_ai_failure()` 在每次 HTTP 失败时自增，连续 3 次就全局
     熔断 —— 10 个账号的池只试到第 2 个就被关掉，「池」形同虚设。
  2. 无模型轮换：只认单个 `OPENROUTER_MODEL`；`core.utils.get_model_chain()` 早已
     实现模型回退链，translator 却没用；且内置默认链里 5 个模型的 `:free` 变体
     已被 OpenRouter 全部撤下（静默失效）。
  3. 重试放大：请求继承了 `Retry(total=3, status_forcelist=[429])`，一次 429 被放大
     成 4 次请求，还把状态码藏进 ResponseError 里，日志看不出是哪个 key/模型。
  4. 无时间上界：`worst_case = 条数 × 路由数 × 超时`，没有任何一层设限。

本文件把这些行为逐条钉住。全部用例都不联网（tests/conftest.py 有 URL 层守卫）。
"""

from __future__ import annotations

import time
import unittest
from unittest.mock import MagicMock, patch

import pytest
import requests

from core import utils as _utils
from core.normalize import translator as T
from core.normalize.translator import (
    _RouteCursor,
    _ai_translate_batch,
    _get_openrouter_keys,
    _is_circuit_broken,
    _openrouter_session,
    _resolve_model,
    add_bilingual_fields,
    reset_circuit_breaker,
    reset_google_circuit_breaker,
)
from core.utils import DEFAULT_OPENROUTER_MODELS, get_model_chain

# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_dead_model_registry():
    """`core.utils._DEAD_MODELS` 是模块级可变集合，会跨用例泄漏。

    本文件里 404 用例会调用 `mark_model_dead`，如果不清理，后续用例的模型链
    就会莫名其妙少一项 —— 典型的「测试互相污染」。
    """
    _utils._DEAD_MODELS.clear()
    yield
    _utils._DEAD_MODELS.clear()


def _ok(content: str, *, finish: str = "stop", reasoning: str | None = None) -> MagicMock:
    """构造一个 HTTP 200 的 OpenRouter 响应。"""
    message: dict[str, str] = {"content": content}
    if reasoning is not None:
        message["reasoning"] = reasoning
    resp = MagicMock()
    resp.status_code = 200
    resp.text = "ok"
    resp.json.return_value = {"choices": [{"message": message, "finish_reason": finish}]}
    return resp


def _err(status: int, text: str = "", headers: dict | None = None) -> MagicMock:
    """构造一个非 200 的 OpenRouter 响应。"""
    resp = MagicMock()
    resp.status_code = status
    resp.text = text or f"HTTP {status}"
    resp.json.return_value = {"error": {"message": resp.text}}
    # 必须是真 dict：真实响应的 .headers 是 CaseInsensitiveDict，
    # 若用 MagicMock，`.get()` 会返回一个 truthy 的 Mock，把限额信息污染成假数据。
    resp.headers = dict(headers or {})
    return resp


# 429 有**两个来源**，处置方向完全相反，判据只能是响应头是否存在。
# 这两组替身把两种来源区分开，是「淘汰 key 还是淘汰 model」的唯一依据。
_PLATFORM_LIMIT_HEADERS = {
    "X-RateLimit-Limit": "20",
    "X-RateLimit-Remaining": "0",
    "X-RateLimit-Reset": "7",
}


def _platform_429(text: str = "rate limited") -> MagicMock:
    """OpenRouter **平台**限额（按 key）：带 X-RateLimit-* → 该换 key。"""
    return _err(429, text, headers=_PLATFORM_LIMIT_HEADERS)


def _provider_429(text: str = "upstream provider saturated") -> MagicMock:
    """**上游 provider** 限流（按 model）：无 X-RateLimit-* → 该换 model。

    实测 run 36964009801 的 429 就长这样。旧代码把它当成平台限额 → 换 key，
    于是 10 个账号全撞在同一个被限流的模型上 → AI 翻译 0 条。
    """
    return _err(429, text)


def _batch_reply(count: int, prefix: str = "译文") -> str:
    """按约定的「编号. 译文」格式生成批量翻译结果。"""
    return "\n".join(f"{i + 1}. {prefix}{i + 1}" for i in range(count))


def _session(responses: list) -> MagicMock:
    """构造一个按脚本顺序吐响应的假 session；脚本用尽即断言失败。"""
    queue = list(responses)

    def _next(*_args, **_kwargs):
        if not queue:
            raise AssertionError("mock session 被调用次数超过了脚本中的响应数量")
        return queue.pop(0)

    session = MagicMock(spec=requests.Session)
    session.post.side_effect = _next
    return session


def _items(count: int) -> list[dict[str, object]]:
    return [
        {"title": f"English Title {i}", "url": f"https://example.com/{i}"}
        for i in range(count)
    ]


def _models_used(session: MagicMock) -> list[str]:
    return [call.kwargs["json"]["model"] for call in session.post.call_args_list]


def _keys_used(session: MagicMock) -> list[str]:
    return [
        call.kwargs["headers"]["Authorization"].removeprefix("Bearer ")
        for call in session.post.call_args_list
    ]


# ---------------------------------------------------------------------------
# 1. 模型链配置
# ---------------------------------------------------------------------------


class ModelChainConfigurationTests(unittest.TestCase):
    """默认模型链必须真的可用 —— 上一版 5 个模型全部已下线却无人察觉。"""

    def test_default_chain_has_at_least_four_models(self):
        self.assertGreaterEqual(len(DEFAULT_OPENROUTER_MODELS), 4)

    def test_default_chain_entries_are_free_tier(self):
        """每一项要么带 `:free` 后缀，要么是官方免费模型路由器。"""
        for model in DEFAULT_OPENROUTER_MODELS:
            self.assertTrue(
                model.endswith(":free") or model == "openrouter/free",
                f"{model} 不是免费模型 —— 免费额度是零成本架构的前提",
            )

    def test_default_chain_has_no_duplicates(self):
        self.assertEqual(len(DEFAULT_OPENROUTER_MODELS), len(set(DEFAULT_OPENROUTER_MODELS)))

    def test_get_model_chain_prefers_plural_env_over_singular(self):
        with patch.dict(
            "os.environ",
            {"OPENROUTER_MODELS": "a/one:free,b/two:free", "OPENROUTER_MODEL": "c/three:free"},
        ):
            chain = get_model_chain()
        self.assertEqual(chain[:2], ["a/one:free", "b/two:free"])
        self.assertIn("c/three:free", chain)

    def test_get_model_chain_falls_back_to_singular_env(self):
        with patch.dict("os.environ", {"OPENROUTER_MODELS": "", "OPENROUTER_MODEL": "solo/model:free"}):
            self.assertEqual(get_model_chain(), ["solo/model:free"])

    def test_get_model_chain_dedupes_preserving_order(self):
        with patch.dict(
            "os.environ", {"OPENROUTER_MODELS": "x:free,y:free,x:free", "OPENROUTER_MODEL": "y:free"}
        ):
            self.assertEqual(get_model_chain(), ["x:free", "y:free"])

    def test_resolve_model_uses_chain_head(self):
        with patch.dict("os.environ", {"OPENROUTER_MODELS": "first/model:free,second/model:free"}):
            self.assertEqual(_resolve_model(None), "first/model:free")
            # 显式指定的模型优先于链
            self.assertEqual(_resolve_model("explicit/model:free"), "explicit/model:free")

    def test_keys_are_deduped(self):
        with patch.dict("os.environ", {"OPENROUTER_KEYS": "k1, k2 ,k1,,k3"}):
            self.assertEqual(_get_openrouter_keys(), ["k1", "k2", "k3"])

    def test_translator_uses_the_chain_not_a_single_model(self):
        """请求体里的 model 必须来自模型链，而不是某个写死的常量。"""
        session = _session([_ok(_batch_reply(1))])
        with patch.object(T, "_openrouter_session", return_value=session), \
                patch.dict("os.environ", {"OPENROUTER_MODELS": "chain/head:free,chain/tail:free"}):
            _ai_translate_batch(session, ["Hello world"], "k1")
        self.assertEqual(_models_used(session), ["chain/head:free"])


# ---------------------------------------------------------------------------
# 2. 路由池
# ---------------------------------------------------------------------------


class RouteCursorTests(unittest.TestCase):
    def test_consecutive_attempts_change_both_key_and_model(self):
        """核心性质：连续两次尝试必须**同时**换 key 和换 model。

        这是「不把池浪费在注定失败的组合上」的保证。旧的 model-major 顺序
        （先把同一个模型的所有 key 试完）在 run 36964009801 里让 10 个账号
        全撞在被上游限流的同一个模型上 → AI 翻译 0 条。
        """
        cursor = _RouteCursor(["k1", "k2", "k3"], ["m1", "m2", "m3"], 60)
        routes = [cursor.next_route() for _ in range(6)]
        for prev, nxt in zip(routes, routes[1:]):
            self.assertNotEqual(prev[0], nxt[0], f"key 没换：{prev} → {nxt}")
            self.assertNotEqual(prev[1], nxt[1], f"model 没换：{prev} → {nxt}")

    def test_order_is_a_shifted_sweep(self):
        """错位扫描：外层 model 偏移量、内层 key 下标。"""
        cursor = _RouteCursor(["k1", "k2"], ["m1", "m2"], 60)
        self.assertEqual(
            [cursor.next_route() for _ in range(4)],
            [("k1", "m1"), ("k2", "m2"), ("k1", "m2"), ("k2", "m1")],
        )

    def test_capacity_is_keys_times_models(self):
        """覆盖必须是完整的 K × M —— 朴素对角线只有 lcm(K, M) 个互异组合。"""
        cursor = _RouteCursor(["k1", "k2"], ["m1", "m2", "m3"], 60)
        self.assertEqual(cursor.total_routes, 6)
        taken = []
        while (route := cursor.next_route()) is not None:
            taken.append(route)
        self.assertEqual(len(taken), 6)
        self.assertEqual(len(set(taken)), 6, "2 key × 3 model 必须走出 6 个互异组合")

    def test_small_pool_still_covers_every_combination(self):
        """2×2 是朴素对角线的反例（周期 lcm(2,2)=2）—— 这里必须走满 4 个。"""
        cursor = _RouteCursor(["k1", "k2"], ["m1", "m2"], 60)
        taken = []
        while (route := cursor.next_route()) is not None:
            taken.append(route)
        self.assertEqual(len(set(taken)), 4)

    def test_key_exhaustion_removes_every_route_using_that_key(self):
        cursor = _RouteCursor(["k1", "k2"], ["m1", "m2"], 60)
        cursor.mark_key_exhausted("k1")
        taken = []
        while (route := cursor.next_route()) is not None:
            taken.append(route)
        self.assertTrue(taken, "还有 k2 可用，不该一条路由都给不出来")
        self.assertEqual({k for k, _ in taken}, {"k2"}, "k1 已被淘汰，不该再出现")
        self.assertEqual({m for _, m in taken}, {"m1", "m2"}, "两个模型都该被试到")
        self.assertEqual(cursor.live_keys, 1)

    def test_model_death_removes_every_route_using_that_model(self):
        cursor = _RouteCursor(["k1", "k2"], ["m1", "m2"], 60)
        cursor.mark_model_dead("m1")
        taken = []
        while (route := cursor.next_route()) is not None:
            taken.append(route)
        self.assertTrue(taken)
        self.assertEqual({m for _, m in taken}, {"m2"}, "m1 已被淘汰，不该再出现")
        self.assertEqual({k for k, _ in taken}, {"k1", "k2"}, "两个 key 都该被试到")
        self.assertEqual(cursor.live_models, 1)

    def test_retired_model_is_synced_to_the_global_registry(self):
        """400/404 = 模型名无效 → 其他消费方（notifier）也不该再用它。"""
        cursor = _RouteCursor(["k1"], ["m1", "m2"], 60)
        cursor.mark_model_dead("m1", retired=True)
        self.assertIn("m1", _utils._DEAD_MODELS)

    def test_provider_rate_limited_model_is_not_globally_blacklisted(self):
        """provider 限流只是**这一轮**不可用，模型本身没坏。

        若同步给全局注册表，会连带压制 notifier / analyst_agent 对该模型的使用 ——
        把「暂时忙」误报成「已下线」，正是「把未知伪装成已知」的老毛病。
        """
        cursor = _RouteCursor(["k1"], ["m1", "m2"], 60)
        cursor.mark_model_dead("m1", reason="provider busy", retired=False)
        self.assertIn("m1", cursor.dead_models, "本轮路由池里必须被淘汰")
        self.assertNotIn("m1", _utils._DEAD_MODELS, "但不该进全局黑名单")

    def test_pool_is_empty_when_all_keys_are_exhausted(self):
        cursor = _RouteCursor(["k1", "k2"], ["m1", "m2"], 60)
        cursor.mark_key_exhausted("k1")
        cursor.mark_key_exhausted("k2")
        self.assertFalse(cursor.available())
        self.assertIsNone(cursor.next_route())

    def test_pool_is_empty_when_all_models_are_dead(self):
        cursor = _RouteCursor(["k1", "k2"], ["m1"], 60)
        cursor.mark_model_dead("m1")
        self.assertFalse(cursor.available())
        self.assertIsNone(cursor.next_route())

    def test_zero_budget_closes_the_pool_immediately(self):
        cursor = _RouteCursor(["k1"], ["m1"], 0)
        self.assertTrue(cursor.out_of_budget())
        self.assertFalse(cursor.available())
        self.assertIsNone(cursor.next_route())

    def test_summary_is_human_readable(self):
        cursor = _RouteCursor(["k1", "k2"], ["m1"], 60)
        cursor.mark_key_exhausted("k1")
        text = cursor.summary()
        self.assertIn("可用 key 1/2", text)
        self.assertIn("可用模型 1/1", text)


# ---------------------------------------------------------------------------
# 3. 失败归因：429 只怪 key，404 只怪模型
# ---------------------------------------------------------------------------


class FailureAttributionTests(unittest.TestCase):
    def setUp(self):
        reset_circuit_breaker()
        reset_google_circuit_breaker()

    def tearDown(self):
        reset_circuit_breaker()
        reset_google_circuit_breaker()

    def test_platform_429_marks_key_exhausted_but_not_model(self):
        """带 X-RateLimit-* 的 429 = 平台按账号计的限额 → 只淘汰 key。"""
        cursor = _RouteCursor(["k1", "k2"], ["m1"], 60)
        session = _session([_platform_429()])
        with patch.object(T, "_openrouter_session", return_value=session):
            outcome: dict = {}
            _ai_translate_batch(session, ["Hi"], "k1", model="m1", outcome=outcome)
        self.assertEqual(outcome["status"], "key_exhausted")
        T._apply_route_outcome(cursor, "k1", "m1", outcome)
        self.assertEqual(cursor.dead_keys, {"k1"})
        self.assertEqual(cursor.dead_models, set())
        self.assertTrue(cursor.available(), "还有 k2 可用，池不该被判死")

    def test_provider_429_marks_model_dead_but_not_key(self):
        """不带 X-RateLimit-* 的 429 = 上游 provider 按模型限流 → 只淘汰 model。

        换 key 无用：同一模型对任何账号都会被上游拒。旧代码在这里换 key，
        于是 10 个账号全浪费在同一个被限流的模型上（run 36964009801）。
        """
        cursor = _RouteCursor(["k1", "k2"], ["m1", "m2"], 60)
        session = _session([_provider_429()])
        with patch.object(T, "_openrouter_session", return_value=session):
            outcome: dict = {}
            _ai_translate_batch(session, ["Hi"], "k1", model="m1", outcome=outcome)
        self.assertEqual(outcome["status"], "provider_rate_limited")
        T._apply_route_outcome(cursor, "k1", "m1", outcome)
        self.assertEqual(cursor.dead_models, {"m1"}, "该淘汰模型")
        self.assertEqual(cursor.dead_keys, set(), "key 没问题，不该淘汰")
        self.assertTrue(cursor.available(), "还有 m2 可用")

    def test_429_records_the_platform_limit_headers(self):
        """429 必须把 X-RateLimit-* 记下来。

        没有这三个数就分不清「每日额度用完了」和「每分钟限额」——
        前者要等次日、要充值；后者只要放慢节奏。实测 10 个账号各剩 50/50
        日额度却全部 429，正是靠这个区分才能定位。
        """
        session = _session([_platform_429()])
        with patch.object(T, "_openrouter_session", return_value=session):
            outcome: dict = {}
            _ai_translate_batch(session, ["Hi"], "k1", model="m1", outcome=outcome)
        self.assertEqual(outcome["status"], "key_exhausted")
        self.assertEqual(outcome["X-RateLimit-Limit"], "20")
        self.assertEqual(outcome["X-RateLimit-Remaining"], "0")
        self.assertEqual(outcome["X-RateLimit-Reset"], "7")

    def test_429_without_limit_headers_is_attributed_to_the_provider(self):
        """没有 X-RateLimit-* 头就不能猜成平台限额 —— 猜错方向等于废掉整个池。"""
        session = _session([_provider_429()])
        with patch.object(T, "_openrouter_session", return_value=session):
            outcome: dict = {}
            _ai_translate_batch(session, ["Hi"], "k1", model="m1", outcome=outcome)
        self.assertEqual(outcome["status"], "provider_rate_limited")
        self.assertNotIn("X-RateLimit-Limit", outcome)

    def test_429_with_only_retry_after_is_attributed_to_the_provider(self):
        """Retry-After 是 provider 侧的信号，单独出现也算上游限流。"""
        session = _session([_err(429, "slow down", headers={"Retry-After": "12"})])
        with patch.object(T, "_openrouter_session", return_value=session):
            outcome: dict = {}
            _ai_translate_batch(session, ["Hi"], "k1", model="m1", outcome=outcome)
        self.assertEqual(outcome["status"], "provider_rate_limited")
        self.assertEqual(outcome["Retry-After"], "12")

    def test_404_marks_model_dead_but_not_key(self):
        cursor = _RouteCursor(["k1"], ["m1", "m2"], 60)
        session = _session([_err(404, "No endpoints found")])
        with patch.object(T, "_openrouter_session", return_value=session):
            outcome: dict = {}
            _ai_translate_batch(session, ["Hi"], "k1", model="m1", outcome=outcome)
        self.assertEqual(outcome["status"], "model_error")
        T._apply_route_outcome(cursor, "k1", "m1", outcome)
        self.assertEqual(cursor.dead_models, {"m1"})
        self.assertEqual(cursor.dead_keys, set())
        self.assertTrue(cursor.available(), "还有 m2 可用，池不该被判死")

    def test_network_error_kills_neither_dimension(self):
        """一次网络抖动不能断言「这个 key 或这个模型坏了」。"""
        cursor = _RouteCursor(["k1"], ["m1"], 60)
        session = MagicMock(spec=requests.Session)
        session.post.side_effect = requests.ConnectionError("boom")
        with patch.object(T, "_openrouter_session", return_value=session):
            outcome: dict = {}
            _ai_translate_batch(session, ["Hi"], "k1", model="m1", outcome=outcome)
        self.assertEqual(outcome["status"], "network_error")
        T._apply_route_outcome(cursor, "k1", "m1", outcome)
        self.assertEqual(cursor.dead_keys, set())
        self.assertEqual(cursor.dead_models, set())

    def test_server_error_kills_neither_dimension(self):
        cursor = _RouteCursor(["k1"], ["m1"], 60)
        session = _session([_err(503)])
        with patch.object(T, "_openrouter_session", return_value=session):
            outcome: dict = {}
            _ai_translate_batch(session, ["Hi"], "k1", model="m1", outcome=outcome)
        self.assertEqual(outcome["status"], "server_error")
        T._apply_route_outcome(cursor, "k1", "m1", outcome)
        self.assertEqual(cursor.dead_keys, set())
        self.assertEqual(cursor.dead_models, set())

    def test_empty_content_with_reasoning_is_reported_distinctly(self):
        """思维链吃光 max_tokens 时 content 为空 —— 必须能和「模型返回垃圾」区分开。"""
        session = _session([_ok("", finish="length", reasoning="让我想想……")])
        with patch.object(T, "_openrouter_session", return_value=session):
            outcome: dict = {}
            result = _ai_translate_batch(session, ["Hi"], "k1", model="m1", outcome=outcome)
        self.assertEqual(result, [None])
        self.assertEqual(outcome["status"], "empty_reasoning")
        self.assertEqual(outcome["finish_reason"], "length")

    def test_reasoning_is_disabled_in_the_payload(self):
        session = _session([_ok(_batch_reply(1))])
        with patch.object(T, "_openrouter_session", return_value=session):
            _ai_translate_batch(session, ["Hi"], "k1", model="m1")
        self.assertEqual(session.post.call_args.kwargs["json"]["reasoning"], {"enabled": False})

    def test_reasoning_field_is_dropped_and_retried_on_400(self):
        """个别模型不接受 reasoning 字段 → 摘掉重试，而不是把模型误判为下线。"""
        session = _session([_err(400, "unsupported parameter: reasoning"), _ok(_batch_reply(1))])
        with patch.object(T, "_openrouter_session", return_value=session):
            outcome: dict = {}
            result = _ai_translate_batch(session, ["Hi"], "k1", model="m1", outcome=outcome)
        self.assertEqual(session.post.call_count, 2)
        self.assertNotIn("reasoning", session.post.call_args.kwargs["json"])
        self.assertEqual(result, ["译文1"])
        self.assertEqual(outcome["status"], "ok")

    def test_batch_max_tokens_has_a_floor_for_reasoning_models(self):
        session = _session([_ok(_batch_reply(1))])
        with patch.object(T, "_openrouter_session", return_value=session):
            _ai_translate_batch(session, ["Hi"], "k1", model="m1")
        self.assertGreaterEqual(session.post.call_args.kwargs["json"]["max_tokens"], 512)

    def test_output_without_cjk_is_treated_as_invalid(self):
        """200 且非空，但输出里没有中文 → 不能当成成功。"""
        session = _session([_ok("Sorry, I cannot help with that.")])
        with patch.object(T, "_openrouter_session", return_value=session):
            outcome: dict = {}
            result = T._ai_translate_single(session, "Hello", "k1", model="m1", outcome=outcome)
        self.assertIsNone(result)
        self.assertEqual(outcome["status"], "invalid_output")

    def test_openrouter_session_disables_retries(self):
        """429 必须原样暴露给路由池，不能被 urllib3 吞掉重试。"""
        parent = MagicMock(spec=requests.Session)
        parent.headers = {}
        session = _openrouter_session(parent)
        adapter = session.get_adapter("https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(adapter.max_retries.total, 0)


# ---------------------------------------------------------------------------
# 4. 关键回归：多账号 key 池必须真的被用起来
# ---------------------------------------------------------------------------


class KeyPoolActuallyWorksTests(unittest.TestCase):
    """旧逻辑下这些用例全部会失败（3 次失败即全局熔断，池子被浪费）。"""

    def setUp(self):
        reset_circuit_breaker()
        reset_google_circuit_breaker()

    def tearDown(self):
        reset_circuit_breaker()
        reset_google_circuit_breaker()

    def _run(self, session, items, keys, models, *, max_tries=8, budget=60):
        env = {
            "OPENROUTER_KEYS": ",".join(keys),
            "OPENROUTER_MODELS": ",".join(models),
            "AI_TRANSLATE_ENABLED": "true",
        }
        with patch.object(T, "_openrouter_session", return_value=session), \
                patch.object(T, "_google_session") as mk_google, \
                patch.object(T, "MAX_ROUTE_TRIES_PER_BATCH", max_tries), \
                patch.object(T, "AI_TRANSLATE_BUDGET_SECONDS", budget), \
                patch.dict("os.environ", env):
            mk_google.return_value.get.side_effect = requests.RequestException("google down")
            return add_bilingual_fields(
                items, list(items), session, {}, max_new_translations=len(items)
            )

    def test_pool_survives_more_than_three_consecutive_429s(self):
        """核心回归：前 4 个 key 都被限流，第 5 个仍然要把活干完。

        旧逻辑在第 3 次失败时全局熔断 → AI 翻译 0 条。这正是线上看到的现象。
        这里用**平台限额**（带 X-RateLimit-* 头）的 429：账号级限额，换 key 有用。
        """
        keys = [f"key-{i}" for i in range(1, 6)]
        session = _session([_platform_429()] * 4 + [_ok(_batch_reply(8))])
        ai_out, _all_out, cache = self._run(session, _items(8), keys, ["m1"])

        self.assertFalse(_is_circuit_broken(), "还有可用 key，不该熔断")
        self.assertEqual(len(cache), 8, "8 条标题都应该被 AI 翻译出来")
        self.assertEqual(session.post.call_count, 5, "5 个 key 都被试到才拿到结果")
        self.assertEqual(len(set(_keys_used(session))), 5, "5 个账号都该真的被用到")
        for item in ai_out:
            self.assertIn(" / ", item["title_bilingual"])

    def test_platform_429_works_through_the_key_pool(self):
        """平台 429 是账号级的 → 逐个账号试过去，池子没被判死。

        注意：错位扫描在 key 级失败时**也会**顺带换模型（因为下标每步 +1）。
        这是刻意为之 —— 轮询不假设「哪一维才是坏的」，只保证连续两次尝试
        两条轴都变；模型本身健康，被顺带用上是无害的。
        真正要钉住的是：3 个账号都被**真的**用上、且池没被提前判死。
        """
        session = _session([_platform_429(), _platform_429(), _ok(_batch_reply(3))])
        _ai_out, _all_out, cache = self._run(
            session, _items(3), ["k1", "k2", "k3"], ["m1", "m2"]
        )

        self.assertEqual(session.post.call_count, 3)
        self.assertEqual(len(set(_keys_used(session))), 3, "三个账号都该被试到")
        self.assertEqual(len(cache), 3, "第 3 个账号必须把活干完")
        self.assertFalse(_is_circuit_broken(), "还有账号没试完，不该熔断")

    def test_provider_429_rotates_models_without_burning_every_key(self):
        """上游 provider 限流是模型级的：换 key 没用，必须换模型。

        旧代码把这类 429 当成账号限额 → 3 个账号会逐个撞在同一个被限流的模型上
        全部报废（run 36964009801 的 AI 翻译 0 条）。现在第二次就换模型并成功。
        """
        session = _session([_provider_429(), _ok(_batch_reply(3))])
        self._run(session, _items(3), ["k1", "k2", "k3"], ["m1", "m2"])

        self.assertEqual(_models_used(session), ["m1", "m2"], "被限流的模型只试一次就换")
        self.assertEqual(session.post.call_count, 2, "换模型即可恢复，不该把 3 个账号全烧掉")

    def test_404_rotates_models_within_the_same_key(self):
        """404 是模型级的：换个模型继续用同一个账号。"""
        session = _session([_err(404, "No endpoints found"), _ok(_batch_reply(3))])
        self._run(session, _items(3), ["k1"], ["m1", "m2"])

        self.assertEqual(_models_used(session), ["m1", "m2"])
        self.assertEqual(set(_keys_used(session)), {"k1"})

    def test_breaker_trips_when_every_key_is_exhausted(self):
        session = _session([_platform_429()] * 2)
        ai_out, _all_out, cache = self._run(session, _items(2), ["k1", "k2"], ["m1", "m2"])

        self.assertEqual(session.post.call_count, 2, "两个 key 各试一次就都没了")
        self.assertTrue(_is_circuit_broken())
        self.assertEqual(cache, {})
        for item in ai_out:
            self.assertEqual(item["title_bilingual"], item["title"], "降级为英文原标题")

    def test_breaker_trips_when_every_model_is_rate_limited(self):
        """所有模型都被上游限流 → 池空 → 熔断，而不是死循环重试。"""
        session = _session([_provider_429()] * 2)
        ai_out, _all_out, cache = self._run(session, _items(2), ["k1", "k2"], ["m1", "m2"])

        self.assertEqual(session.post.call_count, 2, "两个模型各试一次就都没了")
        self.assertTrue(_is_circuit_broken())
        self.assertEqual(cache, {})

    def test_every_route_is_consumed_before_giving_up(self):
        """503 不淘汰任何一维 → 四条路由应该被逐条试完，而不是试 3 次就熔断。"""
        session = _session([_err(503)] * 4)
        ai_out, _all_out, cache = self._run(session, _items(2), ["k1", "k2"], ["m1", "m2"])

        self.assertEqual(session.post.call_count, 4, "2 keys × 2 models 全部试过")
        self.assertEqual(len(set(zip(_keys_used(session), _models_used(session)))), 4)
        self.assertTrue(_is_circuit_broken())
        self.assertEqual(cache, {})

    def test_failed_titles_are_retried_on_a_fresh_route(self):
        """第一批全军覆没后，失败的标题要退回队列换新路由，不能被丢掉。"""
        session = _session([_platform_429(), _ok(_batch_reply(8))])
        _ai_out, _all_out, cache = self._run(session, _items(8), ["k1", "k2"], ["m1"])
        self.assertEqual(len(cache), 8)

    def test_partial_batch_results_are_kept(self):
        """模型只译出一半时，已成功的要落缓存，剩余的继续换路由。"""
        session = _session([_ok("1. 甲\n2. 乙"), _ok(_batch_reply(6))])
        _ai_out, _all_out, cache = self._run(session, _items(8), ["k1", "k2"], ["m1"])
        self.assertEqual(len(cache), 8)
        self.assertEqual(cache["English Title 0"], "甲")
        self.assertEqual(cache["English Title 1"], "乙")
        self.assertEqual(session.post.call_count, 2)

    def test_no_keys_means_no_ai_and_no_network(self):
        session = MagicMock(spec=requests.Session)
        with patch.object(T, "_openrouter_session", return_value=session), \
                patch.object(T, "_google_session") as mk_google, \
                patch.dict("os.environ", {"OPENROUTER_KEYS": "", "AI_TRANSLATE_ENABLED": "true"}):
            mk_google.return_value.get.side_effect = requests.RequestException("google down")
            add_bilingual_fields(_items(3), _items(3), session, {}, max_new_translations=3)
        session.post.assert_not_called()

    def test_ai_can_be_switched_off(self):
        session = MagicMock(spec=requests.Session)
        with patch.object(T, "_openrouter_session", return_value=session), \
                patch.object(T, "_google_session") as mk_google, \
                patch.dict(
                    "os.environ",
                    {"OPENROUTER_KEYS": "k1,k2", "OPENROUTER_MODELS": "m1", "AI_TRANSLATE_ENABLED": "false"},
                ):
            mk_google.return_value.get.side_effect = requests.RequestException("google down")
            add_bilingual_fields(_items(3), _items(3), session, {}, max_new_translations=3)
        session.post.assert_not_called()


# ---------------------------------------------------------------------------
# 5. 时间上界：worst_case = 条数 × 路由数 × 超时，必须有人管
# ---------------------------------------------------------------------------


class TimeBoundTests(unittest.TestCase):
    def setUp(self):
        reset_circuit_breaker()
        reset_google_circuit_breaker()

    def tearDown(self):
        reset_circuit_breaker()
        reset_google_circuit_breaker()

    def test_ai_budget_stops_the_phase_without_any_request(self):
        """预算为 0 时一条请求都不该发出去。"""
        session = MagicMock(spec=requests.Session)
        with patch.object(T, "_openrouter_session", return_value=session), \
                patch.object(T, "_google_session") as mk_google, \
                patch.object(T, "AI_TRANSLATE_BUDGET_SECONDS", 0), \
                patch.dict("os.environ", {"OPENROUTER_KEYS": "k1,k2", "OPENROUTER_MODELS": "m1,m2"}):
            mk_google.return_value.get.side_effect = requests.RequestException("google down")
            add_bilingual_fields(_items(5), _items(5), session, {}, max_new_translations=5)

        session.post.assert_not_called()
        self.assertFalse(_is_circuit_broken(), "预算耗尽不是熔断，只是本轮不再尝试")

    def test_slow_endpoint_cannot_blow_past_the_budget(self):
        """端点很慢且一直失败时，请求数必须被预算卡住，而不是把路由池扫干净。"""
        real_sleep = time.sleep  # 先抓住真实 sleep，下面会把模块级 sleep 打成 no-op

        def slow_fail(*_args, **_kwargs):
            real_sleep(0.4)
            return _err(503)

        session = MagicMock(spec=requests.Session)
        session.post.side_effect = slow_fail

        with patch.object(T, "_openrouter_session", return_value=session), \
                patch.object(T, "_google_session") as mk_google, \
                patch.object(time, "sleep", lambda *_a, **_k: None), \
                patch.object(T, "MAX_ROUTE_TRIES_PER_BATCH", 1), \
                patch.object(T, "AI_TRANSLATE_BUDGET_SECONDS", 1.0), \
                patch.dict(
                    "os.environ",
                    {"OPENROUTER_KEYS": "k1,k2,k3,k4,k5", "OPENROUTER_MODELS": "m1,m2,m3,m4"},
                ):
            mk_google.return_value.get.side_effect = requests.RequestException("google down")
            started = time.monotonic()
            add_bilingual_fields(_items(8), _items(8), session, {}, max_new_translations=8)
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, 5.0, "整段耗时应该被 1s 预算卡住")
        self.assertLessEqual(session.post.call_count, 4, "20 条路由不该被扫干净")

    def test_route_attempts_per_batch_are_capped(self):
        """单批最多换 N 条路由，防止一个批次把整轮预算吃光。"""
        session = _session([_err(503)] * 10)
        with patch.object(T, "_openrouter_session", return_value=session), \
                patch.object(T, "_google_session") as mk_google, \
                patch.object(T, "MAX_ROUTE_TRIES_PER_BATCH", 2), \
                patch.object(T, "AI_TRANSLATE_BUDGET_SECONDS", 60), \
                patch.dict("os.environ", {"OPENROUTER_KEYS": "k1,k2,k3,k4", "OPENROUTER_MODELS": "m1"}):
            mk_google.return_value.get.side_effect = requests.RequestException("google down")
            # 2 条标题 → 1 个批次；503 既不淘汰 key 也不淘汰 model
            add_bilingual_fields(_items(2), _items(2), session, {}, max_new_translations=2)

        # 批次上限 2 次 × 可用路由 4 条 → 最多 4 次请求，绝不会是 10 次
        self.assertLessEqual(session.post.call_count, 4)


if __name__ == "__main__":
    unittest.main()

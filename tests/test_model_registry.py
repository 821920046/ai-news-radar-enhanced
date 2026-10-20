"""OpenRouter 模型链运行期健康探测的回归测试。

被测对象：`core/model_registry.py` 与它在 `core.utils.get_model_chain()` 里的接线。

背景（真实事故）：OpenRouter 会定期撤下 `:free` 变体。2026-10-02 内置链 5 个模型
全部下线、AI 翻译 0 条；2026-10-20 实测 8 个配置模型里又有 2 个下线，而流水线
仍在按原链调用。静态清单必然过期，所以探测必须在**运行期**发生，且探测自身
失败时不得掀翻调用方。

全部用例走注入的假 session / 假 probe，不发起真实外网请求（conftest 的 URL 层
守卫会兜底拦住，但被拦住也意味着「测试通过依赖环境」—— 这里连被拦都不允许）。
"""

from __future__ import annotations

import importlib.util
import os
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import requests

from core.model_registry import (
    ChainReport,
    MODELS_API,
    ProbeResult,
    is_chat_capable,
    probe_free_models,
    refresh_model_chain,
    reset_caches,
    resolve_model_chain,
)
from core.utils import DEFAULT_OPENROUTER_MODELS, get_model_chain, mark_model_dead

ROOT = Path(__file__).resolve().parents[1]


def _entry(mid: str, prompt: str = "0", completion: str = "0",
           ctx: int = 8192, out=None) -> dict:
    return {
        "id": mid,
        "name": mid,
        "pricing": {"prompt": prompt, "completion": completion},
        "context_length": ctx,
        "architecture": {"output_modalities": out if out is not None else ["text"]},
    }


class _Resp:
    """最小化的假响应对象。"""

    def __init__(self, payload=None, status: int = 200, json_error=None):
        self._payload = payload
        self.status_code = status
        self._json_error = json_error

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class _Session:
    """按脚本依次返回响应或抛出异常的假 session，并记录每次调用。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    def get(self, url, timeout=None):
        self.calls.append({"url": url, "timeout": timeout})
        item = self.script.pop(0) if self.script else _Resp({"data": []})
        if isinstance(item, Exception):
            raise item
        return item


@contextmanager
def _clean_env(*names: str):
    """临时移除会干扰 get_model_chain 优先级的环境变量。"""
    saved = {n: os.environ.pop(n, None) for n in names}
    try:
        yield
    finally:
        for n, v in saved.items():
            if v is not None:
                os.environ[n] = v


class ChatCapabilityTests(unittest.TestCase):
    """「免费 ≠ 能翻译」的过滤器（唯一实现在 core，脚本只是再导出）。"""

    def test_plain_text_chat_model_is_capable(self):
        self.assertTrue(is_chat_capable(_entry("vendor/model:free")))

    def test_audio_output_is_excluded(self):
        self.assertFalse(is_chat_capable(_entry("google/lyria-3-pro-preview", out=["text", "audio"])))

    def test_missing_text_output_is_excluded(self):
        self.assertFalse(is_chat_capable(_entry("vendor/embed:free", out=["embedding"])))

    def test_content_safety_name_is_excluded(self):
        self.assertFalse(is_chat_capable(_entry("nvidia/nemotron-3.5-content-safety:free")))

    def test_missing_architecture_defaults_to_capable(self):
        entry = _entry("vendor/model:free")
        entry.pop("architecture")
        self.assertTrue(is_chat_capable(entry))

    def test_script_uses_the_same_implementation(self):
        """防止巡检脚本又长出一份自己的副本（本项目「4 处配置必须一致」同类教训）。"""
        spec = importlib.util.spec_from_file_location(
            "check_openrouter_models", ROOT / "scripts" / "check_openrouter_models.py"
        )
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        self.assertIs(checker.is_chat_capable, is_chat_capable)
        self.assertEqual(checker.MODELS_API, MODELS_API)


class ProbeTests(unittest.TestCase):
    """探测本身：解析、过滤、失败归因。"""

    def test_free_chat_models_are_collected(self):
        session = _Session([_Resp({"data": [
            _entry("vendor-a/live:free", ctx=100000),
            _entry("vendor-b/paid", prompt="0.000001"),
            _entry("vendor-c/audio:free", out=["text", "audio"]),
            _entry("nvidia/nemotron-3.5-content-safety:free"),
            {"id": None, "pricing": {"prompt": "0", "completion": "0"}},
        ]})])
        result = probe_free_models(session=session)
        self.assertTrue(result.ok)
        self.assertEqual(result.total_models, 5)
        # 免费的 3 个（live / audio / safety）都会被计入，但只有 live 可对话
        self.assertEqual(result.total_free, 3)
        self.assertEqual(list(result.entries), ["vendor-a/live:free"])
        self.assertEqual(result.attempts, 1)
        self.assertEqual(session.calls[0]["url"], MODELS_API)

    def test_timeout_is_honoured(self):
        session = _Session([_Resp({"data": []})])
        probe_free_models(session=session, timeout=3.5)
        self.assertEqual(session.calls[0]["timeout"], 3.5)

    def test_http_error_is_reported_not_raised(self):
        session = _Session([_Resp(status=503), _Resp(status=503)])
        result = probe_free_models(session=session)
        self.assertFalse(result.ok)
        self.assertIn("HTTP 503", result.error)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(len(session.calls), 2)

    def test_network_error_is_reported_not_raised(self):
        boom = requests.ConnectionError("connection reset")
        session = _Session([boom, boom])
        result = probe_free_models(session=session)
        self.assertFalse(result.ok)
        self.assertIn("ConnectionError", result.error)

    def test_retry_recovers_from_transient_failure(self):
        session = _Session([requests.ConnectionError("reset"), _Resp({"data": [_entry("a/b:free")]})])
        result = probe_free_models(session=session)
        self.assertTrue(result.ok)
        self.assertEqual(result.attempts, 2)
        self.assertIn("a/b:free", result.entries)

    def test_malformed_payload_is_reported(self):
        session = _Session([_Resp({"models": "not-a-list"}), _Resp({"models": "not-a-list"})])
        result = probe_free_models(session=session)
        self.assertFalse(result.ok)
        self.assertIn("结构异常", result.error)

    def test_broken_json_is_reported(self):
        session = _Session([_Resp(json_error=ValueError("no json")), _Resp(json_error=ValueError("no json"))])
        result = probe_free_models(session=session)
        self.assertFalse(result.ok)
        self.assertIn("ValueError", result.error)


class RefreshChainTests(unittest.TestCase):
    """纯函数：模型链的剔除与补位。"""

    ENTRIES = {
        "vendor-a/one:free": _entry("vendor-a/one:free", ctx=100000),
        "vendor-a/two:free": _entry("vendor-a/two:free", ctx=90000),
        "vendor-b/three:free": _entry("vendor-b/three:free", ctx=80000),
        "vendor-b/four:free": _entry("vendor-b/four:free", ctx=70000),
        "vendor-c/five:free": _entry("vendor-c/five:free", ctx=60000),
    }

    def _probe(self, ok=True, error=""):
        return ProbeResult(ok=ok, entries=dict(self.ENTRIES) if ok else {}, error=error)

    def test_live_configured_kept_in_order_and_dead_dropped(self):
        chain = ["vendor-a/one:free", "dead/x:free", "vendor-b/three:free"]
        report = refresh_model_chain(chain, self._probe(), target=2)
        self.assertEqual(report.live, ["vendor-a/one:free", "vendor-b/three:free"])
        self.assertEqual(report.dead, ["dead/x:free"])
        # target=2 恰好被活配置填满，没有补位空间
        self.assertEqual(report.chain, ["vendor-a/one:free", "vendor-b/three:free"])
        self.assertEqual(report.alternates, [])

    def test_alternates_fill_up_to_target(self):
        chain = ["vendor-a/one:free"]
        report = refresh_model_chain(chain, self._probe(), target=4)
        # 按 context 降序补位：two(90k) > three(80k) > four(70k)
        self.assertEqual(report.alternates, ["vendor-a/two:free", "vendor-b/three:free", "vendor-b/four:free"])
        self.assertEqual(report.chain, ["vendor-a/one:free", "vendor-a/two:free",
                                        "vendor-b/three:free", "vendor-b/four:free"])

    def test_provider_diversity_cap_is_respected(self):
        chain = ["vendor-a/one:free", "vendor-a/two:free"]  # vendor-a 已满
        report = refresh_model_chain(chain, self._probe(), target=8)
        self.assertEqual(report.alternates, ["vendor-b/three:free", "vendor-b/four:free", "vendor-c/five:free"])
        for alt in report.alternates:
            self.assertFalse(alt.startswith("vendor-a/"))

    def test_alternates_never_duplicate_configured(self):
        chain = ["vendor-a/one:free", "dead/y:free"]
        report = refresh_model_chain(chain, self._probe(), target=2)
        self.assertNotIn("vendor-a/one:free", report.alternates)

    def test_probe_failure_returns_chain_untouched(self):
        chain = ["dead/a:free", "dead/b:free"]
        report = refresh_model_chain(chain, self._probe(ok=False, error="HTTP 503"))
        self.assertEqual(report.chain, chain)
        self.assertFalse(report.probe_ok)
        self.assertEqual(report.probe_error, "HTTP 503")
        self.assertEqual(report.alternates, [])

    def test_empty_result_is_never_returned(self):
        """全死且无补位时退回原链 —— 宁可用回不新鲜的链，也不能返回空链。"""
        chain = ["dead/a:free", "dead/b:free"]
        report = refresh_model_chain(chain, ProbeResult(ok=True, entries={}), target=8)
        self.assertEqual(report.chain, chain)

    def test_all_configured_dead_still_yields_a_usable_chain(self):
        chain = ["dead/a:free", "dead/b:free"]
        report = refresh_model_chain(chain, self._probe(), target=3)
        self.assertEqual(report.dead, chain)
        self.assertNotIn("dead/a:free", report.chain)
        self.assertTrue(report.chain)  # 非空

    def test_report_changed_flag(self):
        unchanged = refresh_model_chain(["vendor-a/one:free"], self._probe(), target=1)
        self.assertFalse(unchanged.changed)
        dropped = refresh_model_chain(["dead/x:free", "vendor-a/one:free"], self._probe(), target=1)
        self.assertTrue(dropped.changed)

    def test_runtime_dead_model_is_not_resurrected_as_alternate(self):
        """运行期已 400/404 的模型不能被探针的「静态在线」判定请回来。"""
        report = refresh_model_chain(["dead/y:free"], self._probe(), target=3,
                                     exclude={"vendor-a/one:free"})
        self.assertNotIn("vendor-a/one:free", report.chain)
        self.assertEqual(report.live, [])
        # 补位跳过被排除的 one，从剩下的候选里按 context 降序挑
        self.assertEqual(report.chain,
                         ["vendor-a/two:free", "vendor-b/three:free", "vendor-b/four:free"])

    def test_exclude_drops_probe_live_configured_model(self):
        report = refresh_model_chain(["vendor-a/one:free"], self._probe(), target=2,
                                     exclude={"vendor-a/one:free"})
        self.assertEqual(report.live, [])
        self.assertEqual(report.chain, ["vendor-a/two:free", "vendor-b/three:free"][:2])


class _ResolveTestCase(unittest.TestCase):
    def setUp(self):
        reset_caches()
        self.addCleanup(reset_caches)


class ResolveModelChainTests(_ResolveTestCase):
    """进程内 memo、开关与「永不抛异常」。"""

    def test_probe_failure_keeps_the_chain(self):
        failed = ProbeResult(ok=False, error="HTTP 503")
        with patch("core.model_registry.probe_free_models", return_value=failed):
            self.assertEqual(resolve_model_chain(["a:free", "b:free"]), ["a:free", "b:free"])

    def test_probe_success_reconciles_the_chain(self):
        ok = ProbeResult(ok=True, entries={"vendor-a/one:free": _entry("vendor-a/one:free")})
        with patch("core.model_registry.probe_free_models", return_value=ok):
            self.assertEqual(resolve_model_chain(["dead/x:free"]), ["vendor-a/one:free"])

    def test_probe_runs_exactly_once_per_process(self):
        ok = ProbeResult(ok=True, entries={"vendor-a/one:free": _entry("vendor-a/one:free")})
        with patch("core.model_registry.probe_free_models", return_value=ok) as probe:
            resolve_model_chain(["a:free"])
            resolve_model_chain(["b:free"])
            resolve_model_chain(["c:free"])
        self.assertEqual(probe.call_count, 1)

    def test_disabled_probe_makes_zero_requests(self):
        with patch.dict(os.environ, {"OPENROUTER_MODEL_PROBE": "0"}), \
                patch("core.model_registry.probe_free_models") as probe:
            self.assertEqual(resolve_model_chain(["a:free", "b:free"]), ["a:free", "b:free"])
        probe.assert_not_called()

    def test_unexpected_error_never_propagates(self):
        with patch("core.model_registry.probe_free_models", side_effect=RuntimeError("boom")):
            self.assertEqual(resolve_model_chain(["a:free"]), ["a:free"])

    def test_runtime_exclude_is_honoured(self):
        ok = ProbeResult(ok=True, entries={"vendor-a/one:free": _entry("vendor-a/one:free"),
                                           "vendor-b/two:free": _entry("vendor-b/two:free")})
        with patch("core.model_registry.probe_free_models", return_value=ok):
            chain = resolve_model_chain(["vendor-a/one:free"], exclude={"vendor-a/one:free"})
        self.assertEqual(chain, ["vendor-b/two:free"])

    def test_empty_chain_short_circuits(self):
        with patch("core.model_registry.probe_free_models") as probe:
            self.assertEqual(resolve_model_chain([]), [])
        probe.assert_not_called()


class GetModelChainWiringTests(_ResolveTestCase):
    """`get_model_chain()` 最后一环真的接上了探测。"""

    @staticmethod
    def _probe(live_ids, extra=()):
        entries = {mid: _entry(mid) for mid in list(live_ids) + list(extra)}
        return ProbeResult(ok=True, entries=entries, total_models=468, total_free=20)

    def test_dead_default_models_are_dropped_and_replaced(self):
        live = DEFAULT_OPENROUTER_MODELS[:2]
        probe = self._probe(live, extra=["other-vendor/fresh:free"])
        with _clean_env("OPENROUTER_MODEL", "OPENROUTER_MODELS"), \
                patch("core.model_registry.probe_free_models", return_value=probe):
            chain = get_model_chain()
        self.assertEqual(chain, live + ["other-vendor/fresh:free"])
        for dead in DEFAULT_OPENROUTER_MODELS[2:]:
            self.assertNotIn(dead, chain)

    def test_probe_failure_returns_the_configured_chain(self):
        with _clean_env("OPENROUTER_MODEL", "OPENROUTER_MODELS"), \
                patch("core.model_registry.probe_free_models",
                      return_value=ProbeResult(ok=False, error="HTTP 503")):
            self.assertEqual(get_model_chain(), list(DEFAULT_OPENROUTER_MODELS))

    def test_explicitly_configured_dead_model_is_dropped(self):
        probe = self._probe([], extra=["other-vendor/fresh:free"])
        with _clean_env("OPENROUTER_MODEL", "OPENROUTER_MODELS"), \
                patch("core.model_registry.probe_free_models", return_value=probe):
            chain = get_model_chain("gone/retired:free")
        self.assertEqual(chain, ["other-vendor/fresh:free"])

    def test_runtime_dead_models_are_filtered_before_probe(self):
        live = DEFAULT_OPENROUTER_MODELS[0]
        probe = self._probe([live], extra=["other-vendor/fresh:free"])
        from core.utils import _DEAD_MODELS

        before = set(_DEAD_MODELS)

        def _restore():
            # _DEAD_MODELS 是进程级状态，必须还原，否则污染同进程后续测试
            _DEAD_MODELS.clear()
            _DEAD_MODELS.update(before)

        self.addCleanup(_restore)
        mark_model_dead(live)
        with _clean_env("OPENROUTER_MODEL", "OPENROUTER_MODELS"), \
                patch("core.model_registry.probe_free_models", return_value=probe):
            chain = get_model_chain()
        self.assertNotIn(live, chain)
        self.assertIn("other-vendor/fresh:free", chain)


if __name__ == "__main__":
    unittest.main()

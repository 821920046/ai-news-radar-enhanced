"""`scripts/check_openrouter_models.py` 的回归测试（全部离线）。

真正需要被测的不是网络，而是：
  1. **分类逻辑** —— 模型「不存在」和「存在但不再免费」是两种不同的故障，
     都必须被判为不健康；
  2. **退出码语义** —— 「查不了」必须是 2 而不是 0。把「无法确定」当成
     「没问题」正是本项目反复踩的「不可观测 ⇒ 不可断言」；
  3. **四处配置不许漂移** —— core/utils.py 的常量与 config/*.yaml 必须一致，
     否则改了 yaml 没改代码（或反过来）会再次静默失效。
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

# scripts/ 不是包，用 importlib 按路径加载
_spec = importlib.util.spec_from_file_location(
    "check_openrouter_models", ROOT / "scripts" / "check_openrouter_models.py"
)
assert _spec and _spec.loader
checker = importlib.util.module_from_spec(_spec)
sys.modules["check_openrouter_models"] = checker
_spec.loader.exec_module(checker)

from core.utils import DEFAULT_OPENROUTER_MODELS  # noqa: E402


def _entry(model_id: str, prompt: str = "0", completion: str = "0") -> dict:
    return {"id": model_id, "pricing": {"prompt": prompt, "completion": completion}}


class ClassifyTests(unittest.TestCase):
    def test_healthy_model(self):
        buckets = checker.classify(
            [("m/free:free", "origin")],
            {"m/free:free": _entry("m/free:free")},
            {"m/free:free": _entry("m/free:free")},
        )
        self.assertEqual([m for m, _ in buckets["healthy"]], ["m/free:free"])
        self.assertEqual(buckets["missing"], [])
        self.assertEqual(buckets["no_longer_free"], [])

    def test_model_that_no_longer_exists_is_missing(self):
        """这正是 2026-10-02 事故的形态：`:free` 变体被撤下，模型名直接不存在。"""
        buckets = checker.classify(
            [("dead/model:free", "core/utils.py")],
            {"dead/model": _entry("dead/model", "0.000001", "0.000002")},
            {},
        )
        self.assertEqual([m for m, _ in buckets["missing"]], ["dead/model:free"])

    def test_model_that_became_paid_is_flagged_separately(self):
        buckets = checker.classify(
            [("paid/model:free", "config/sources.yaml")],
            {"paid/model:free": _entry("paid/model:free", "0.000001", "0.000002")},
            {},
        )
        self.assertEqual(buckets["missing"], [])
        self.assertEqual([m for m, _ in buckets["no_longer_free"]], ["paid/model:free"])

    def test_origin_is_preserved_for_actionable_reporting(self):
        buckets = checker.classify(
            [("gone:free", "config/model_config.yaml:openrouter.models")],
            {},
            {},
        )
        self.assertEqual(buckets["missing"][0][1], "config/model_config.yaml:openrouter.models")


class ExitCodeTests(unittest.TestCase):
    """退出码必须能区分「有问题」和「查不了」。"""

    def setUp(self):
        # main() 会自己 parse_args()，测试里必须把 pytest 的 argv 换掉
        patcher = patch.object(sys, "argv", ["check_openrouter_models.py"])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_network_failure_exits_2_not_0(self):
        with patch.object(checker, "_load_configured_models", return_value=[("m:free", "origin")]), \
                patch.object(checker, "_fetch_free_models", side_effect=RuntimeError("network down")):
            self.assertEqual(checker.main(), 2)

    def test_all_healthy_exits_0(self):
        entry = {"m:free": _entry("m:free")}
        with patch.object(checker, "_load_configured_models", return_value=[("m:free", "origin")]), \
                patch.object(checker, "_fetch_free_models", return_value=(entry, entry)):
            self.assertEqual(checker.main(), 0)

    def test_missing_model_exits_1(self):
        with patch.object(checker, "_load_configured_models", return_value=[("gone:free", "origin")]), \
                patch.object(checker, "_fetch_free_models", return_value=({}, {})):
            self.assertEqual(checker.main(), 1)

    def test_no_configured_models_exits_2(self):
        """解析不到配置 = 无法断言，不能报成功。"""
        with patch.object(checker, "_load_configured_models", return_value=[]):
            self.assertEqual(checker.main(), 2)


class ConfigSourcesAgreeTests(unittest.TestCase):
    """四处配置必须指向同一批模型 —— 上一版就是因为到处漂移而静默失效。"""

    def test_yaml_models_match_the_code_registry(self):
        raw = checker._load_configured_models()
        from_code = {model for model, origin in raw if origin.startswith("core/utils.py")}
        from_yaml = {model for model, origin in raw if origin.startswith("config/")}
        self.assertTrue(from_yaml, "config/*.yaml 里应该配置了模型链")
        self.assertEqual(
            from_yaml, from_code,
            f"yaml 与 core/utils.py 的模型清单不一致：\n"
            f"  只在 yaml 里：{sorted(from_yaml - from_code)}\n"
            f"  只在代码里：{sorted(from_code - from_yaml)}",
        )

    def test_every_configured_model_is_declared_free(self):
        for model, _origin in checker._load_configured_models():
            self.assertTrue(
                model.endswith(":free") or model == "openrouter/free",
                f"{model} 没带 :free 后缀，说明它被改成付费模型了",
            )

    def test_registry_still_has_at_least_four_models(self):
        self.assertGreaterEqual(len(DEFAULT_OPENROUTER_MODELS), 4)


if __name__ == "__main__":
    unittest.main()

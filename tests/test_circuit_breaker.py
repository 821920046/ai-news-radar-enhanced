"""Unit tests for AI Translation Circuit Breaker & Fail-safe Mechanisms."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import requests

from core.normalize.translator import (
    MAX_CONSECUTIVE_FAILURES,
    _ai_translate_batch,
    _ai_translate_single,
    _is_circuit_broken,
    _mark_ai_failure,
    _mark_ai_success,
    _try_translate_desc,
    add_bilingual_fields,
    reset_circuit_breaker,
)


class CircuitBreakerTests(unittest.TestCase):
    def setUp(self):
        reset_circuit_breaker()

    def tearDown(self):
        reset_circuit_breaker()

    def test_failure_counter_triggers_circuit_breaker(self):
        """验证连续失败达阈值后触发熔断器。"""
        self.assertFalse(_is_circuit_broken())

        for i in range(MAX_CONSECUTIVE_FAILURES - 1):
            broken = _mark_ai_failure()
            self.assertFalse(broken)
            self.assertFalse(_is_circuit_broken())

        broken = _mark_ai_failure()
        self.assertTrue(broken)
        self.assertTrue(_is_circuit_broken())

    def test_success_resets_consecutive_failure_counter(self):
        """验证成功调用会清零连续失败计数。"""
        _mark_ai_failure()
        _mark_ai_failure()
        self.assertFalse(_is_circuit_broken())

        _mark_ai_success()

        # 再失败两次仍不会触发（因为此前已被清零）
        _mark_ai_failure()
        _mark_ai_failure()
        self.assertFalse(_is_circuit_broken())

        # 第三次失败触发
        _mark_ai_failure()
        self.assertTrue(_is_circuit_broken())

    def test_single_and_batch_translate_short_circuit_when_broken(self):
        """验证熔断开启后，单条与批量翻译直接返回 None，完全不发起网络请求。"""
        # 手动触发熔断
        for _ in range(MAX_CONSECUTIVE_FAILURES):
            _mark_ai_failure()
        self.assertTrue(_is_circuit_broken())

        mock_session = MagicMock(spec=requests.Session)

        # 单条翻译短路
        res = _ai_translate_single(mock_session, "OpenAI releases GPT-5", "fake-key")
        self.assertIsNone(res)
        mock_session.post.assert_not_called()

        # 批量翻译短路
        batch_res = _ai_translate_batch(mock_session, ["Title 1", "Title 2"], "fake-key")
        self.assertEqual(batch_res, [None, None])
        mock_session.post.assert_not_called()

    def test_desc_translate_short_circuit_when_broken(self):
        """验证熔断开启后，文章 description 翻译立即短路退出，不再轮询 key。"""
        for _ in range(MAX_CONSECUTIVE_FAILURES):
            _mark_ai_failure()

        mock_session = MagicMock(spec=requests.Session)
        item = {"description": "This is a very long english description about AI breakthrough."}

        _try_translate_desc(mock_session, item, ["key1", "key2", "key3"], start_key_idx=0)
        # 没有任何 post 调用
        mock_session.post.assert_not_called()
        # 描述保持原样
        self.assertEqual(item["description"], "This is a very long english description about AI breakthrough.")

    @patch("core.normalize.translator._google_session")
    @patch("core.normalize.translator._openrouter_session")
    @patch("core.normalize.translator._get_openrouter_keys", return_value=["test-key-1"])
    @patch.dict("os.environ", {"AI_TRANSLATE_ENABLED": "true"})
    def test_add_bilingual_fields_graceful_degradation_on_429(
        self, _mock_keys, mk_openrouter_session, mk_google_session
    ):
        """验证当所有 key 都被 429 限流时，add_bilingual_fields 能熔断并优雅降级。

        ⚠️ OpenRouter 走**独立 session**（v3.3 引入，用于去掉继承来的 3 次重试，
        让 429 原样暴露给路由池），所以 mock 主 session 的 `.post` 不再能拦到请求。
        必须直接 patch `_openrouter_session`，否则该用例会真的联网 —— 而
        tests/conftest.py 的联网守卫会直接抛 RuntimeError。
        """
        mock_session = MagicMock(spec=requests.Session)
        # 模拟每次调用都返回 429（= 这个账号的免费额度用完了）
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.text = "rate limited"
        mock_session.post.return_value = mock_resp
        mk_openrouter_session.return_value = mock_session
        # Google 也失败作为极端用例（独立 session，需单独 patch）
        mk_google_session.return_value.get.side_effect = requests.RequestException("google down")

        items_ai = [
            {"title": f"English Title {i}", "url": f"https://example.com/{i}", "description": "English desc " * 5}
            for i in range(10)
        ]
        items_all = list(items_ai)
        cache = {}

        ai_out, all_out, new_cache = add_bilingual_fields(items_ai, items_all, mock_session, cache, max_new_translations=10)

        # 唯一的 key 被淘汰后无路可走 → 触发熔断
        self.assertTrue(_is_circuit_broken())
        # 数据不会丢失或崩溃，降级为原标题
        self.assertEqual(len(ai_out), 10)
        for it in ai_out:
            self.assertEqual(it["title_bilingual"], it["title"])


if __name__ == "__main__":
    unittest.main()

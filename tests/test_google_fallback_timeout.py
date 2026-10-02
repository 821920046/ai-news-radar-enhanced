"""Regression tests for the Google Translate fallback hang (v3.2).

Background — the incident this file locks down
----------------------------------------------
Run 36890199782 ("Update AI News Snapshot") was killed by `timeout-minutes: 40`
after 40m24s, with step 8 "Update data" cancelled. The final log line before a
39-minute silence was:

    [Translate] 熔断器已触发，剩余 56 条标题切换到 Google Translate

Root cause was a multiplicative blowup in `translate_to_zh_cn`:

  * it reused `create_session()`, which mounts `Retry(total=3, backoff_factor=0.8)`
  * plus a per-request `timeout=12`
  * and it was called **sequentially, one title at a time** from `enrich()`

So a single unreachable endpoint cost ≈ 3 retries x (12s + 0.8/1.6/3.2s backoff)
≈ 44s, and 56 titles serialised to ≈ 41 minutes — matching the observed ~39 min.

These tests assert the three structural guards that prevent a recurrence:
  1. Google requests must NOT inherit the 3x retry policy  (no 44s per title)
  2. Google must have its own circuit breaker               (no x56 linear blowup)
  3. The whole Google phase must respect a wall-clock budget (bounded worst case)
"""

from __future__ import annotations

import time
import unittest
from unittest.mock import MagicMock, patch

import requests

from core.normalize import translator as T
from core.normalize.translator import (
    GOOGLE_MAX_CONSECUTIVE_FAILURES,
    GOOGLE_TIMEOUT,
    _google_session,
    add_bilingual_fields,
    is_google_circuit_broken,
    reset_circuit_breaker,
    reset_google_circuit_breaker,
    translate_to_zh_cn,
)


class GoogleSessionHasNoRetriesTests(unittest.TestCase):
    """Guard 1: the Google session must not reuse the 3x retry policy."""

    def test_google_session_disables_retries(self):
        parent = MagicMock(spec=requests.Session)
        parent.headers = {"User-Agent": "test-agent"}

        gs = _google_session(parent)
        try:
            adapter = gs.get_adapter("https://translate.googleapis.com/")
            # urllib3 Retry object must be configured for zero retries
            self.assertEqual(adapter.max_retries.total, 0)
            self.assertEqual(adapter.max_retries.connect, 0)
            self.assertEqual(adapter.max_retries.read, 0)
        finally:
            gs.close()

    def test_google_session_inherits_headers(self):
        parent = MagicMock(spec=requests.Session)
        parent.headers = {"User-Agent": "radar-ua", "Accept-Language": "zh-CN"}
        gs = _google_session(parent)
        try:
            self.assertEqual(gs.headers["User-Agent"], "radar-ua")
        finally:
            gs.close()

    def test_google_timeout_is_short(self):
        """Timeout must stay small; the old value of 12s x retries was the killer."""
        self.assertLessEqual(GOOGLE_TIMEOUT, 8)


class GoogleCircuitBreakerTests(unittest.TestCase):
    """Guard 2: Google needs its own breaker, independent from the AI one."""

    def setUp(self):
        reset_google_circuit_breaker()
        reset_circuit_breaker()

    def tearDown(self):
        reset_google_circuit_breaker()
        reset_circuit_breaker()

    def test_repeated_failures_trip_google_breaker(self):
        session = MagicMock(spec=requests.Session)
        # Force an exception inside translate_to_zh_cn
        with patch.object(T, "_google_session") as mk:
            mk.return_value.get.side_effect = requests.Timeout("boom")
            for _ in range(GOOGLE_MAX_CONSECUTIVE_FAILURES):
                self.assertIsNone(translate_to_zh_cn(session, "Some English title"))
        self.assertTrue(is_google_circuit_broken())

    def test_broken_breaker_short_circuits_without_network(self):
        session = MagicMock(spec=requests.Session)
        with patch.object(T, "_google_session") as mk:
            mk.return_value.get.side_effect = requests.Timeout("boom")
            for _ in range(GOOGLE_MAX_CONSECUTIVE_FAILURES):
                translate_to_zh_cn(session, "Title A")

            mk.reset_mock()
            # Once broken, no further network access should occur at all
            self.assertIsNone(translate_to_zh_cn(session, "Title B"))
            self.assertIsNone(translate_to_zh_cn(session, "Title C"))
            mk.assert_not_called()

    def test_success_resets_google_failure_counter(self):
        session = MagicMock(spec=requests.Session)
        ok = MagicMock()
        ok.raise_for_status.return_value = None
        ok.json.return_value = [[["你好世界", "Hello world", None, None]]]

        with patch.object(T, "_google_session") as mk:
            mk.return_value.get.return_value = ok
            T._mark_google_failure()
            T._mark_google_failure()
            self.assertFalse(is_google_circuit_broken())
            # A success clears the streak
            self.assertEqual(translate_to_zh_cn(session, "Hello world"), "你好世界")
            self.assertFalse(is_google_circuit_broken())

    def test_ai_and_google_breakers_are_independent(self):
        """Tripping the AI breaker must not pre-trip the Google one."""
        reset_circuit_breaker()
        reset_google_circuit_breaker()
        for _ in range(T.MAX_CONSECUTIVE_FAILURES):
            T._mark_ai_failure()
        self.assertTrue(T._is_circuit_broken())
        self.assertFalse(is_google_circuit_broken())


class GooglePhaseBudgetTests(unittest.TestCase):
    """Guard 3: the Google phase must honour a wall-clock budget."""

    def setUp(self):
        reset_google_circuit_breaker()
        reset_circuit_breaker()

    def tearDown(self):
        reset_google_circuit_breaker()
        reset_circuit_breaker()

    @patch.dict("os.environ", {"AI_TRANSLATE_ENABLED": "false", "OPENROUTER_KEYS": ""})
    @patch.object(T, "GOOGLE_BUDGET_SECONDS", 1.0)
    @patch.object(T, "_google_session")
    def test_budget_caps_total_wall_clock(self, mk_gs):
        """Even with a pathologically slow endpoint, the phase returns fast."""

        def slow_get(*_a, **_kw):
            time.sleep(0.6)  # each request is slow but not infinite
            raise requests.Timeout("slow")

        mk_gs.return_value.get.side_effect = slow_get

        session = MagicMock(spec=requests.Session)
        items = [
            {"title": f"English Title Number {i}", "url": f"https://example.com/{i}", "description": ""}
            for i in range(60)
        ]
        t0 = time.monotonic()
        ai_out, all_out, _cache = add_bilingual_fields(
            items, list(items), session, {}, max_new_translations=60
        )
        elapsed = time.monotonic() - t0

        # The budget is 1s; allow generous slack for thread teardown (pool.map
        # drains in-flight work), but it must be nowhere near 60 x 0.6s = 36s.
        self.assertLess(elapsed, 10.0, f"Google phase took {elapsed:.1f}s, budget not enforced")
        # No data loss / no crash: every item survives with a usable title
        self.assertEqual(len(ai_out), 60)
        for it in ai_out:
            self.assertTrue(it["title_bilingual"])

    @patch.dict("os.environ", {"AI_TRANSLATE_ENABLED": "false", "OPENROUTER_KEYS": ""})
    @patch.object(T, "_google_session")
    def test_failure_does_not_crash_pipeline(self, mk_gs):
        mk_gs.return_value.get.side_effect = RuntimeError("unexpected")
        session = MagicMock(spec=requests.Session)
        items = [{"title": "Some English Title", "url": "https://example.com/x", "description": ""}]
        ai_out, _all_out, _cache = add_bilingual_fields(
            items, list(items), session, {}, max_new_translations=5
        )
        self.assertEqual(len(ai_out), 1)
        self.assertEqual(ai_out[0]["title_bilingual"], "Some English Title")

    @patch.dict("os.environ", {"AI_TRANSLATE_ENABLED": "false", "OPENROUTER_KEYS": ""})
    @patch.object(T, "_google_session")
    def test_successful_parallel_prefetch_populates_cache(self, mk_gs):
        """Titles that succeed via the prefetch must be applied by enrich()."""
        ok = MagicMock()
        ok.raise_for_status.return_value = None
        ok.json.return_value = [[["并行翻译结果", "parallel", None, None]]]
        mk_gs.return_value.get.return_value = ok

        session = MagicMock(spec=requests.Session)
        items = [
            {"title": f"Parallel English Title {i}", "url": f"https://example.com/{i}", "description": ""}
            for i in range(12)
        ]
        ai_out, _all_out, cache = add_bilingual_fields(
            items, list(items), session, {}, max_new_translations=12
        )
        self.assertEqual(len(ai_out), 12)
        for it in ai_out:
            self.assertEqual(it["title_zh"], "并行翻译结果")
            self.assertTrue(it["title_bilingual"].startswith("并行翻译结果 / "))

    @patch.dict("os.environ", {"AI_TRANSLATE_ENABLED": "false", "OPENROUTER_KEYS": ""})
    @patch.object(T, "_google_session")
    def test_prefetch_runs_requests_concurrently(self, mk_gs):
        """Guard against a regression back to sequential per-title calls."""
        import threading

        lock = threading.Lock()
        state = {"current": 0, "peak": 0}

        def counted_get(*_a, **_kw):
            with lock:
                state["current"] += 1
                state["peak"] = max(state["peak"], state["current"])
            time.sleep(0.15)
            with lock:
                state["current"] -= 1
            raise requests.Timeout("nope")

        mk_gs.return_value.get.side_effect = counted_get

        session = MagicMock(spec=requests.Session)
        items = [
            {"title": f"Concurrent English Title {i}", "url": f"https://example.com/{i}", "description": ""}
            for i in range(16)
        ]
        add_bilingual_fields(items, list(items), session, {}, max_new_translations=16)
        # With serial execution peak would be 1; the pool must overlap requests.
        self.assertGreater(state["peak"], 1, "Google prefetch appears to be sequential again")


if __name__ == "__main__":
    unittest.main()

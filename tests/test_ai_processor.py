import os
import unittest
from unittest.mock import patch

from scripts.ai_processor import KeyPoolManager, generate_tldr, process_items_with_ai


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class FakeRequester:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return self.responses.pop(0)


class AiProcessorTests(unittest.TestCase):
    def test_key_pool_dedupes_and_marks_exhausted(self):
        pool = KeyPoolManager("k1, k2, k1")
        self.assertEqual(pool.keys, ["k1", "k2"])
        self.assertEqual(pool.get_key(), "k1")
        pool.mark_exhausted("k2")
        self.assertEqual(pool.get_key(), "k1")
        pool.mark_exhausted("k1")
        self.assertIsNone(pool.get_key())
        self.assertTrue(pool.is_all_exhausted())

    def test_generate_tldr_rotates_after_rate_limit(self):
        requester = FakeRequester(
            [
                FakeResponse(429, text="rate limited"),
                FakeResponse(
                    200,
                    {
                        "choices": [
                            {"message": {"content": "摘要：OpenAI发布新模型"}}
                        ]
                    },
                ),
            ]
        )
        pool = KeyPoolManager("key-one,key-two")
        result = generate_tldr(
            "OpenAI released a new model for developers with better tool use and lower latency.",
            pool,
            session=requester,
            model="test/model",
        )
        self.assertEqual(result, "OpenAI发布新模型")
        self.assertEqual(len(requester.calls), 2)
        self.assertEqual(pool.exhausted_keys, {"key-one"})

    def test_process_items_without_keys_is_noop(self):
        items = [{"title": "OpenAI releases a model", "description": "A short update"}]
        with patch.dict(os.environ, {}, clear=True):
            self.assertIs(process_items_with_ai(items), items)
        self.assertNotIn("tldr", items[0])

    def test_process_items_only_selected_top_n(self):
        items = [
            {
                "title": "Low signal",
                "description": "This item has enough content but less heat than the other item.",
                "hotness_score": 1,
            },
            {
                "title": "High signal",
                "description": "This OpenAI item has enough content and should be selected first.",
                "hotness_score": 500,
            },
        ]
        env = {"OPENROUTER_KEYS": "k1", "AI_TLDR_TOP_N": "1", "AI_TLDR_MAX_WORKERS": "1"}
        with patch.dict(os.environ, env, clear=True):
            with patch("scripts.ai_processor.generate_tldr", return_value="High signal summary"):
                process_items_with_ai(items)
        self.assertNotIn("tldr", items[0])
        self.assertEqual(items[1]["tldr"], "High signal summary")


if __name__ == "__main__":
    unittest.main()

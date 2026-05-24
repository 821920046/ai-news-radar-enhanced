import unittest

from scripts.notifier import (
    build_markdown_message,
    build_webhook_payload,
    filter_breaking_news,
    select_digest_items,
)


class NotifierTests(unittest.TestCase):
    def test_filter_breaking_news_uses_score_tags_and_dedupes_titles(self):
        items = [
            {"title": "Same", "hotness_score": 200, "tags": []},
            {"title": "Same", "hotness_score": 300, "tags": []},
            {"title": "Model launch", "hotness_score": 0, "tags": ["模型发布"]},
            {"title": "Quiet", "hotness_score": 1, "tags": []},
        ]
        out = filter_breaking_news(items, hotness_threshold=150)
        self.assertEqual([item["title"] for item in out], ["Same", "Model launch"])

    def test_select_digest_items_prefers_hotness_then_time(self):
        items = [
            {"title": "Old hot", "hotness_score": 300, "published_at": "2026-05-01T00:00:00Z"},
            {"title": "New warm", "hotness_score": 200, "published_at": "2026-05-02T00:00:00Z"},
        ]
        out = select_digest_items(items, limit=1)
        self.assertEqual(out[0]["title"], "Old hot")

    def test_build_markdown_message_includes_tldr_and_link(self):
        message = build_markdown_message(
            [
                {
                    "title": "OpenAI update",
                    "url": "https://example.com/a",
                    "site_name": "Official",
                    "hotness_score": 350,
                    "tldr": "OpenAI发布新能力",
                }
            ],
            title="Digest",
        )
        self.assertIn("**Digest**", message)
        self.assertIn("[OpenAI update](https://example.com/a)", message)
        self.assertIn("TL;DR: OpenAI发布新能力", message)

    def test_build_webhook_payload_shapes(self):
        self.assertEqual(
            build_webhook_payload("hello", "wechat"),
            {"msgtype": "markdown", "markdown": {"content": "hello"}},
        )
        self.assertEqual(
            build_webhook_payload("hello", "feishu"),
            {"msg_type": "text", "content": {"text": "hello"}},
        )


if __name__ == "__main__":
    unittest.main()

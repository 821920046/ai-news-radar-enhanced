import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.utils import (
    extract_image_url_from_html,
    make_item_id,
    normalize_image_url,
    normalize_url,
    parse_date_any,
    parse_relative_time_zh,
)
from scripts.fetchers.opml import parse_opml_subscriptions


class UtilsTests(unittest.TestCase):
    def test_normalize_url_removes_tracking(self):
        raw = "https://example.com/path?a=1&utm_source=x&fbclid=abc"
        self.assertEqual(normalize_url(raw), "https://example.com/path?a=1")

    def test_make_item_id_stable(self):
        a = make_item_id("site", "src", "Title", "https://a.com?p=1&utm_source=x")
        b = make_item_id("site", "src", "Title", "https://a.com?p=1")
        self.assertEqual(a, b)

    def test_parse_relative_time_zh_minutes(self):
        now = datetime(2026, 2, 19, 12, 0, tzinfo=timezone.utc)
        dt = parse_relative_time_zh("8分钟前", now)
        self.assertEqual(dt, datetime(2026, 2, 19, 11, 52, tzinfo=timezone.utc))

    def test_parse_date_any_english_rfc_not_misparsed_as_today(self):
        now = datetime(2026, 2, 21, 4, 30, tzinfo=timezone.utc)
        dt = parse_date_any("Tue, 07 Oct 2025 03:00:00 GMT", now)
        self.assertEqual(dt, datetime(2025, 10, 7, 3, 0, tzinfo=timezone.utc))

    def test_parse_opml_subscriptions(self):
        opml = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0"><body>
<outline text="A" title="A" xmlUrl="https://a.com/feed.xml" />
<outline text="A2" title="A2" xmlUrl="https://a.com/feed.xml" />
<outline text="B" xmlUrl="https://b.com/rss" />
</body></opml>"""
        with TemporaryDirectory() as td:
            p = Path(td) / "x.opml"
            p.write_text(opml, encoding="utf-8")
            feeds = parse_opml_subscriptions(p)
        self.assertEqual(len(feeds), 2)
        self.assertEqual(feeds[0]["title"], "A")
        self.assertEqual(feeds[1]["title"], "B")

    def test_extract_image_url_from_html(self):
        html = '<p>Intro</p><img src="/cover.jpg" alt="cover">'
        self.assertEqual(extract_image_url_from_html(html, "https://example.com/post"), "https://example.com/cover.jpg")

    def test_extract_image_url_prefers_og_image(self):
        html = """
        <meta property="og:image" content="https://cdn.example.com/news.png">
        <img src="/inline.jpg" alt="inline">
        """
        self.assertEqual(extract_image_url_from_html(html, "https://example.com/post"), "https://cdn.example.com/news.png")

    def test_normalize_image_url_rejects_inline_data(self):
        self.assertEqual(normalize_image_url("data:image/png;base64,abc"), "")

    def test_title_cache_pruning_logic(self):
        # 模拟当前 archive 中的活跃新闻
        archive = {
            "id_1": {"title": "OpenAI releases GPT-5"},
            "id_2": {"title": "Claude 4 launches"},
        }
        # 模拟包含很多旧翻译的 translation cache
        title_cache = {
            "OpenAI releases GPT-5": "OpenAI 发布 GPT-5",
            "Claude 4 launches": "Claude 4 发布",
            "Some old expired news": "一些过期的旧新闻",
        }

        # 执行淘汰过滤逻辑
        archive_titles = {record.get("title") for record in archive.values() if record.get("title")}
        pruned_cache = {k: v for k, v in title_cache.items() if k in archive_titles}

        # 验证淘汰结果
        self.assertIn("OpenAI releases GPT-5", pruned_cache)
        self.assertIn("Claude 4 launches", pruned_cache)
        self.assertNotIn("Some old expired news", pruned_cache)
        self.assertEqual(len(pruned_cache), 2)


if __name__ == "__main__":
    unittest.main()

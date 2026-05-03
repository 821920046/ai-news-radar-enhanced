import unittest

from scripts.update_news import (
    build_latest_payloads,
    dedupe_items_by_title_url,
    is_ai_related_record,
    is_hubtoday_generic_anchor_title,
    is_hubtoday_placeholder_title,
    maybe_fix_mojibake,
    normalize_source_for_display,
    parse_ai_breakfast_items,
    parse_feed_entries_via_xml,
    parse_anthropic_news_items,
    parse_follow_builders_items,
    parse_openai_codex_changelog_items,
    redact_public_text,
)


class TopicFilterTests(unittest.TestCase):
    def test_accepts_ai_keyword(self):
        rec = {
            "site_id": "techurls",
            "site_name": "TechURLs",
            "source": "Hacker News",
            "title": "OpenAI releases new GPT model",
            "url": "https://example.com/ai",
        }
        self.assertTrue(is_ai_related_record(rec))

    def test_accepts_copilot_keyword(self):
        rec = {
            "site_id": "official_ai",
            "site_name": "Official AI Updates",
            "source": "GitHub Changelog",
            "title": "GitHub Copilot adds a new coding agent",
            "url": "https://example.com/copilot",
        }
        self.assertTrue(is_ai_related_record(rec))

    def test_accepts_robotics_keyword(self):
        rec = {
            "site_id": "newsnow",
            "site_name": "NewsNow",
            "source": "technology",
            "title": "Embodied robotics gets new funding",
            "url": "https://example.com/robotics",
        }
        self.assertTrue(is_ai_related_record(rec))

    def test_accepts_follow_builders_curated_feed(self):
        rec = {
            "site_id": "followbuilders",
            "site_name": "Follow Builders",
            "source": "Follow Builders · X · Andrej Karpathy",
            "title": "A terse but useful Codex builder note",
            "url": "https://x.com/karpathy/status/1",
        }
        self.assertTrue(is_ai_related_record(rec))

    def test_rejects_noise_topic(self):
        rec = {
            "site_id": "tophub",
            "site_name": "TopHub",
            "source": "微博热搜",
            "title": "明星八卦今日热搜",
            "url": "https://example.com/noise",
        }
        self.assertFalse(is_ai_related_record(rec))

    def test_rejects_commerce_noise(self):
        rec = {
            "site_id": "tophub",
            "site_name": "TopHub",
            "source": "淘宝 ‧ 天猫 · 热销总榜",
            "title": "白象拌面任选加码 券后¥29.96",
            "url": "https://example.com/shop",
        }
        self.assertFalse(is_ai_related_record(rec))

    def test_zeli_only_24h_hot(self):
        keep = {
            "site_id": "zeli",
            "site_name": "Zeli",
            "source": "Hacker News · 24h最热",
            "title": "AI Agent for code search",
            "url": "https://example.com/a",
        }
        drop = {
            "site_id": "zeli",
            "site_name": "Zeli",
            "source": "HN New",
            "title": "AI Agent for code search",
            "url": "https://example.com/b",
        }
        self.assertTrue(is_ai_related_record(keep))
        self.assertFalse(is_ai_related_record(drop))

    def test_buzzing_source_fallback_to_host(self):
        source = normalize_source_for_display("buzzing", "Buzzing", "https://news.ycombinator.com/item?id=1")
        self.assertEqual(source, "news.ycombinator.com")

    def test_fix_mojibake(self):
        raw = "è°å¨ç¼åä»£ç "
        self.assertEqual(maybe_fix_mojibake(raw), "谁在编写代码")

    def test_parse_feed_entries_via_xml(self):
        xml = b"""<?xml version='1.0' encoding='UTF-8'?>
<rss><channel>
<item><title>A</title><link>https://x/a</link><pubDate>2026-02-20</pubDate></item>
</channel></rss>"""
        items = parse_feed_entries_via_xml(xml)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "A")

    def test_parse_atom_feed_entries_via_xml(self):
        xml = b"""<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>A</title><link href="https://x/a" /><updated>2026-02-20</updated></entry>
</feed>"""
        items = parse_feed_entries_via_xml(xml)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "A")
        self.assertEqual(items[0]["link"], "https://x/a")

    def test_parse_anthropic_news_items(self):
        html = """
        <a href="/news/claude-opus-4-7">
          <time>Apr 16, 2026</time>
          <h2>Introducing Claude Opus 4.7</h2>
        </a>
        <a href="/news">News</a>
        """
        items = parse_anthropic_news_items(html, now=None)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].source, "Anthropic News")
        self.assertEqual(items[0].title, "Introducing Claude Opus 4.7")
        self.assertEqual(items[0].url, "https://www.anthropic.com/news/claude-opus-4-7")

    def test_parse_openai_codex_changelog_items(self):
        html = """
        <div id="codex-changelog-content">
          <li id="codex-2026-05-01">
            <time>2026-05-01</time>
            <h3><span>Codex app adds workspace companions</span></h3>
          </li>
        </div>
        """
        items = parse_openai_codex_changelog_items(html, now=None)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].source, "OpenAI Codex Changelog")
        self.assertEqual(items[0].title, "Codex app adds workspace companions")
        self.assertEqual(items[0].url, "https://developers.openai.com/codex/changelog#codex-2026-05-01")

    def test_parse_ai_breakfast_items(self):
        markdown = """
        [May 1, 2026 • 4 min read ### **Anthropic update lands** AI Breakfast](https://aibreakfast.beehiiv.com/p/anthropic-update-lands)
        [Apr 29, 2026 • 5 min read ### **OpenAI ships a model update** AI Breakfast](https://aibreakfast.beehiiv.com/p/openai-ships-model-update)
        """
        items = parse_ai_breakfast_items(markdown, now=None)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].source, "AI Breakfast")
        self.assertEqual(items[0].title, "Anthropic update lands")
        self.assertEqual(items[0].url, "https://aibreakfast.beehiiv.com/p/anthropic-update-lands")

    def test_parse_follow_builders_items(self):
        feeds = {
            "x": {
                "x": [
                    {
                        "name": "Andrej Karpathy",
                        "handle": "karpathy",
                        "tweets": [
                            {
                                "text": "LLM notes from the field",
                                "createdAt": "2026-05-02T06:21:22.000Z",
                                "url": "https://x.com/karpathy/status/1",
                            }
                        ],
                    }
                ]
            },
            "blogs": {
                "generatedAt": "2026-05-02T07:41:11.599Z",
                "blogs": [
                    {
                        "name": "Anthropic Engineering",
                        "title": "A Claude Code postmortem",
                        "url": "https://www.anthropic.com/engineering/postmortem",
                        "publishedAt": None,
                    }
                ],
            },
            "podcasts": {
                "podcasts": [
                    {
                        "name": "No Priors",
                        "title": "Inference cloud interview",
                        "url": "https://www.youtube.com/watch?v=abc",
                        "publishedAt": "2026-05-01T19:34:00.000Z",
                    }
                ]
            },
        }
        items = parse_follow_builders_items(feeds, now=None)
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0].site_id, "followbuilders")
        self.assertEqual(items[0].source, "Follow Builders · X · Andrej Karpathy")
        self.assertEqual(items[1].source, "Follow Builders · Blog · Anthropic Engineering")
        self.assertEqual(items[2].source, "Follow Builders · Podcast · No Priors")

    def test_hubtoday_placeholder_title(self):
        self.assertTrue(is_hubtoday_placeholder_title("详情见官方介绍(AI资讯)"))
        self.assertTrue(is_hubtoday_placeholder_title("查看详情"))
        self.assertFalse(is_hubtoday_placeholder_title("OpenAI 发布 GPT-5o"))
        self.assertTrue(is_hubtoday_generic_anchor_title("论文已公开(AI资讯)"))
        self.assertFalse(is_hubtoday_generic_anchor_title("Anthropic禁止第三方调用订阅。"))

    def test_dedupe_items_by_title_url_latest(self):
        items = [
            {
                "id": "1",
                "title": "Same",
                "title_original": "Same",
                "url": "https://example.com/a",
                "published_at": "2026-02-20T00:00:00Z",
            },
            {
                "id": "2",
                "title": "Same",
                "title_original": "Same",
                "url": "https://example.com/a",
                "published_at": "2026-02-20T01:00:00Z",
            },
        ]
        out = dedupe_items_by_title_url(items, random_pick=False)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["id"], "2")

    def test_rejects_broad_agent_noise_without_ai_context(self):
        rec = {
            "site_id": "buzzing",
            "site_name": "Buzzing",
            "source": "github.com",
            "title": "New travel agent marketplace launches in Europe",
            "url": "https://example.com/travel-agent",
        }
        self.assertFalse(is_ai_related_record(rec))

    def test_accepts_chinese_model_news_after_noise_tightening(self):
        rec = {
            "site_id": "tophub",
            "site_name": "TopHub",
            "source": "机器之心",
            "title": "新一代推理模型刷新多模态数学基准",
            "url": "https://example.com/reasoning-model",
        }
        self.assertTrue(is_ai_related_record(rec))

    def test_redacts_email_like_public_text(self):
        self.assertEqual(redact_public_text("Contact editor@example.com for access"), "Contact [redacted-email] for access")

    def test_build_latest_payloads_keeps_initial_payload_slim(self):
        latest_payload = {
            "generated_at": "2026-05-03T00:00:00Z",
            "window_hours": 24,
            "total_items": 1,
            "total_items_raw": 3,
            "total_items_all_mode": 2,
            "items_ai": [{"title": "AI post", "url": "https://example.com/a"}],
            "items_all": [{"title": "All post", "url": "https://example.com/b"}],
            "items_all_raw": [{"title": "Raw post", "url": "https://example.com/c"}],
        }
        slim, all_payload = build_latest_payloads(latest_payload)
        self.assertIn("items_ai", slim)
        self.assertNotIn("items_all", slim)
        self.assertNotIn("items_all_raw", slim)
        self.assertEqual(all_payload["items_all"][0]["title"], "All post")
        self.assertEqual(all_payload["items_all_raw"][0]["title"], "Raw post")


if __name__ == "__main__":
    unittest.main()

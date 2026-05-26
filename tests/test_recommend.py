import unittest

from scripts.recommend import build_recommendation_reason, build_signal_score, enrich_recommendation_fields


class RecommendTests(unittest.TestCase):
    def test_reason_uses_article_specific_fact(self):
        record = {
            "title": "OpenRouter 完成 1.13 亿美元融资",
            "tags": ["模型发布", "行业动态"],
            "hotness_score": 400,
            "site_id": "buzzing",
        }
        reason = build_recommendation_reason(record)
        self.assertIn("OpenRouter", reason)
        self.assertIn("1.13 亿美元", reason)

    def test_official_source_gets_higher_score(self):
        base = {"site_id": "buzzing", "tags": [], "hotness_score": 0}
        official = {"site_id": "official_ai", "tags": [], "hotness_score": 0}
        self.assertGreater(build_signal_score(official), build_signal_score(base))

    def test_enriches_items_in_place(self):
        items = [{"site_id": "official_ai", "tags": [], "url": "https://example.com/a"}]
        self.assertIs(enrich_recommendation_fields(items), items)
        self.assertIn("recommendation_reason", items[0])
        self.assertIn("signal_score", items[0])


if __name__ == "__main__":
    unittest.main()

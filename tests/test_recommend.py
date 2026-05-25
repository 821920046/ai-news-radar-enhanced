import unittest

from scripts.recommend import build_recommendation_reason, build_signal_score, enrich_recommendation_fields


class RecommendTests(unittest.TestCase):
    def test_tag_reason_takes_priority(self):
        record = {"tags": ["模型发布"], "hotness_score": 0, "site_id": "buzzing"}
        self.assertIn("模型或产品发布", build_recommendation_reason(record))

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

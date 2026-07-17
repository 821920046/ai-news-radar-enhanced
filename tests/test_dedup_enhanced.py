"""针对去重优化新增能力的回归测试：
- 更强的 canonical URL（http/https、www./m.、amp/index 后缀）
- 子集/超集标题的包含合并
- 描述佐证的跨源合并
- 保护：不相关的新闻不能被误合
"""
import unittest

from core.dedup.deduplicator import (
    dedupe_items_by_title_url,
    compute_containment,
)
from core.dedup.deduplicator import _canonical_dedup_url


class CanonicalUrlTests(unittest.TestCase):
    def test_http_https_and_www_unified(self):
        self.assertEqual(
            _canonical_dedup_url("http://www.example.com/news/1/"),
            _canonical_dedup_url("https://example.com/news/1"),
        )

    def test_mobile_prefix_and_index_suffix(self):
        self.assertEqual(
            _canonical_dedup_url("https://m.example.com/a/index.html"),
            _canonical_dedup_url("https://example.com/a"),
        )

    def test_amp_suffix_stripped(self):
        self.assertEqual(
            _canonical_dedup_url("https://example.com/story/amp"),
            _canonical_dedup_url("https://example.com/story"),
        )

    def test_distinct_urls_stay_distinct(self):
        self.assertNotEqual(
            _canonical_dedup_url("https://example.com/a"),
            _canonical_dedup_url("https://example.com/b"),
        )


class ContainmentScoreTests(unittest.TestCase):
    def test_containment_subset(self):
        # 较短标题的 bigram 完全被较长标题覆盖
        self.assertAlmostEqual(compute_containment("abcdef", "abcdefghi"), 1.0, places=6)

    def test_containment_disjoint(self):
        self.assertEqual(compute_containment("abcdef", "xyzuvw"), 0.0)


class EnhancedDedupeTests(unittest.TestCase):
    def test_layer1_canonical_url_merges_http_https_www(self):
        # 标题完全不同，仅靠 canonical URL 相同而合并
        items = [
            {"id": "a", "site_id": "tophub", "title": "苹果发布全新产品阵容",
             "url": "http://www.example.com/news/1/", "published_at": "2026-05-29T10:00:00Z"},
            {"id": "b", "site_id": "iris", "title": "谷歌云服务发生大规模宕机",
             "url": "https://example.com/news/1", "published_at": "2026-05-29T11:00:00Z"},
        ]
        out = dedupe_items_by_title_url(items)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["source_count"], 2)

    def test_containment_merges_subset_superset_titles(self):
        # 一个标题是另一个的子串（附加了后缀），且长度差异足够大以触发长度剪枝
        items = [
            {"id": "1", "site_id": "tophub", "title": "OpenAI 正式发布 GPT-5 旗舰模型",
             "url": "https://a.com/1", "published_at": "2026-05-29T10:00:00Z"},
            {"id": "2", "site_id": "iris", "title": "OpenAI 正式发布 GPT-5 旗舰模型 附完整性能评测与实测报告总结",
             "url": "https://b.com/2", "published_at": "2026-05-29T11:00:00Z"},
        ]
        out = dedupe_items_by_title_url(items)
        self.assertEqual(len(out), 1)

    def test_description_corroboration_merges_reworded_titles(self):
        # 标题中度相似但未达默认阈值，描述高度一致 => 应合并
        desc = "阿里巴巴发布新一代通义千问大模型，在多项基准测试中大幅领先，支持更长上下文与多模态推理"
        items = [
            {"id": "1", "site_id": "tophub", "title": "阿里巴巴发布通义千问三代模型",
             "description": desc, "url": "https://a.com/1", "published_at": "2026-05-29T10:00:00Z"},
            {"id": "2", "site_id": "iris", "title": "阿里巴巴推出通义千问三代模型",
             "description": desc, "url": "https://b.com/2", "published_at": "2026-05-29T11:00:00Z"},
        ]
        out = dedupe_items_by_title_url(items)
        self.assertEqual(len(out), 1)

    def test_unrelated_items_not_merged(self):
        items = [
            {"id": "1", "site_id": "tophub", "title": "OpenAI 发布 GPT-5",
             "url": "https://a.com/1", "published_at": "2026-05-29T10:00:00Z"},
            {"id": "2", "site_id": "iris", "title": "苹果发布 Vision Pro 2",
             "url": "https://b.com/2", "published_at": "2026-05-29T11:00:00Z"},
            {"id": "3", "site_id": "zeli", "title": "特斯拉 Robotaxi 正式上线",
             "url": "https://c.com/3", "published_at": "2026-05-29T12:00:00Z"},
        ]
        out = dedupe_items_by_title_url(items)
        self.assertEqual(len(out), 3)

    def test_short_generic_title_not_over_merged(self):
        # 短/通用标题不应因包含关系被归入超长无关标题
        items = [
            {"id": "1", "site_id": "tophub", "title": "AI 日报",
             "url": "https://a.com/1", "published_at": "2026-05-29T10:00:00Z"},
            {"id": "2", "site_id": "iris", "title": "AI 日报今日头条大模型行业重磅发布与深度解读汇总",
             "url": "https://b.com/2", "published_at": "2026-05-29T11:00:00Z"},
        ]
        out = dedupe_items_by_title_url(items)
        self.assertEqual(len(out), 2)


if __name__ == "__main__":
    unittest.main()

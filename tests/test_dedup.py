import unittest
from datetime import datetime, timezone
from scripts.dedup import (
    normalize_title_for_dedup,
    compute_title_similarity,
    _pick_best_item,
    merge_items_group,
    dedupe_items_by_title_url,
)

class DedupTests(unittest.TestCase):
    def test_normalize_title_for_dedup(self):
        # 1. 空格和大小写
        self.assertEqual(normalize_title_for_dedup("  PPIO入选非凡产研  "), "ppio入选非凡产研")
        self.assertEqual(normalize_title_for_dedup("Claude Code"), "claudecode")
        # 2. 前缀移除
        self.assertEqual(normalize_title_for_dedup("【重磅】OpenAI发布新模型"), "openai发布新模型")
        self.assertEqual(normalize_title_for_dedup("[程序员]写代码的艺术"), "写代码的艺术")
        # 3. 后缀移除
        self.assertEqual(normalize_title_for_dedup("Claude最新模型发布 -- 快科技 -- 科技改变未来"), "claude最新模型发布")
        self.assertEqual(normalize_title_for_dedup("PPIO入选 | 量子位"), "ppio入选")
        self.assertEqual(normalize_title_for_dedup("宇树科技具身智能体验馆 - 华尔街见闻"), "宇树科技具身智能体验馆")
        # 4. 标点和多空格
        self.assertEqual(normalize_title_for_dedup("PPIO  入选 ， 「 2026 Global AI 100 」"), "ppio入选2026globalai100")

    def test_compute_title_similarity(self):
        # 相同标题
        self.assertEqual(compute_title_similarity("abc", "abc"), 1.0)
        # 空标题
        self.assertEqual(compute_title_similarity("", "abc"), 0.0)
        # 长度差异过大剪枝
        self.assertEqual(compute_title_similarity("abc", "abcdefghijk"), 0.0)
        
        # 相似标题计算 (bigram Jaccard)
        # 共同bigram有18个，并集有22个，比值 18/22 = 0.818
        sim = compute_title_similarity("宇树具身智能体验馆亚洲首店将于5月31日", "宇树科技具身智能体验馆亚洲首店将于5月31日")
        self.assertAlmostEqual(sim, 18/22, places=3)
        self.assertTrue(sim > 0.70)

    def test_pick_best_item(self):
        item_tophub = {
            "id": "1",
            "site_id": "tophub",
            "title": "宇树具身智能体验馆亚洲首店将于 5 月 31 日",
            "published_at": "2026-05-29T12:00:00Z",
            "hotness_score": 50,
            "description": "原文链接" # 占位符 description
        }
        item_official = {
            "id": "2",
            "site_id": "official_ai",
            "title": "宇树具身智能体验馆亚洲首店将于 5 月 31 日",
            "published_at": "2026-05-29T10:00:00Z",
            "hotness_score": 0,
            "description": "宇树科技具身智能亚洲首店开业" # 有效描述
        }
        item_iris = {
            "id": "3",
            "site_id": "iris",
            "title": "宇树具身智能体验馆亚洲首店",
            "published_at": "2026-05-29T11:00:00Z",
            "hotness_score": 80,
            "description": "" # 无描述
        }
        
        # 应该优先挑选 official_ai, 因为它信号源权重最高 (100) 且有有效描述
        best = _pick_best_item([item_tophub, item_official, item_iris])
        self.assertEqual(best["id"], "2")

    def test_merge_items_group(self):
        items = [
            {
                "id": "1",
                "site_id": "tophub",
                "site_name": "TopHub",
                "source": "36kr",
                "url": "https://36kr.com/p/1",
                "tags": ["AI", "机器人"]
            },
            {
                "id": "2",
                "site_id": "iris",
                "site_name": "Info Flow",
                "source": "ithome",
                "url": "https://ithome.com/p/2",
                "tags": ["机器人", "具身智能"]
            }
        ]
        
        merged = merge_items_group(items)
        # tags 应该合并去重
        self.assertEqual(merged["tags"], ["AI", "具身智能", "机器人"])
        # source_count 应为 2
        self.assertEqual(merged["source_count"], 2)
        # merged_sources 应该记录所有的源
        self.assertEqual(len(merged["merged_sources"]), 2)

    def test_dedupe_items_by_title_url_stages(self):
        items = [
            # 1. URL 相同去重 (Layer 1)
            {
                "id": "1",
                "site_id": "tophub",
                "title": "OpenAI发布新模型",
                "url": "https://openai.com/blog/1?utm_source=feed",
                "published_at": "2026-05-29T10:00:00Z",
            },
            {
                "id": "2",
                "site_id": "tophub",
                "title": "OpenAI发布新模型(最新)",
                "url": "https://openai.com/blog/1?from=rss",
                "published_at": "2026-05-29T11:00:00Z",
            },
            # 2. 标题相同 URL 不同去重 (Layer 2)
            {
                "id": "3",
                "site_id": "tophub",
                "title": "PPIO入选非凡产研「2026 Global AI 100」",
                "url": "https://qbitai.com/1",
                "published_at": "2026-05-29T12:00:00Z",
            },
            {
                "id": "4",
                "site_id": "tophub",
                "title": "PPIO 入选非凡产研 「 2026 Global AI 100 」",
                "url": "https://36kr.com/2",
                "published_at": "2026-05-29T13:00:00Z",
            },
            # 3. 标题相似模糊匹配去重 (Layer 3)
            {
                "id": "5",
                "site_id": "tophub",
                "title": "宇树具身智能体验馆亚洲首店将于5月31日",
                "url": "https://36kr.com/3",
                "published_at": "2026-05-29T14:00:00Z",
            },
            {
                "id": "6",
                "site_id": "tophub",
                "title": "宇树科技具身智能体验馆亚洲首店将于5月31日",
                "url": "https://ithome.com/4",
                "published_at": "2026-05-29T14:30:00Z",
            }
        ]
        
        out = dedupe_items_by_title_url(items, similarity_threshold=0.70)
        # 6个items应该被合并为3个：
        # - 1和2归为1个 (URL精确匹配)
        # - 3和4归为1个 (标题极净精确匹配)
        # - 5和6归为1个 (标题模糊相似度匹配)
        self.assertEqual(len(out), 3)
        
        # 检查ID (总是选择最新的或者更高优先级的)
        ids = {item["id"] for item in out}
        self.assertIn("2", ids) # 1和2合并，因为2最新且无其它高优先级字段
        self.assertIn("4", ids) # 3和4合并，因为4最新且无其它高优先级字段
        self.assertIn("6", ids) # 5和6合并，因为6最新且无其它高优先级字段

if __name__ == "__main__":
    unittest.main()

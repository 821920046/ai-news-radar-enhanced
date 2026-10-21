"""slim payload 瘦身回归（移动端「打开很慢」的实锤修复）。

两个症状（2026-10-21 对抗移动端加载实测）：

1. `latest-24h.json` 把同一份条目序列化了**两遍**：`main_pipeline` 的
   `latest_payload` 里 `"items"` 与 `"items_ai"` 是同一个列表对象，
   `json.dumps` 照单全收 —— 1310 条时主快照 3.1MB，其中一半是纯重复，
   移动网络每次打开都白传一倍。
2. 每条 item 还带着没人读的 `signal_breakdown`（五维细分，只有 llms.txt 的
   文档表格提过它）与 `compact_score`（recommend.py 写入后零消费方），
   ~120B/条 ≈ 130KB 白传输。

所有消费方（app.js / build_feeds / prerender / validate_data /
send_daily_digest / api/app.py）都优先读 `items_ai`，因此直接删键是安全的。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.output import build_latest_payloads  # noqa: E402


def _item(**over):
    item = {
        "site_id": "tophub", "site_name": "TopHub", "source": "Readhub",
        "title": "OpenAI ships GPT-6", "title_zh": "OpenAI 发布 GPT-6",
        "title_en": None, "url": "https://a.test/1",
        "published_at": None, "first_seen_at": "2026-10-21T00:00:00Z",
        "category": "AI", "tags": ["行业动态"],
        "hotness_score": 3, "hotness_raw": "热榜 #1",
        "signal_score": 28.0, "signal_level": "C",
        "recommendation_reason": "值得扫一眼", "description": "desc",
        # 以下三个是本次要拆掉的死重
        "signal_breakdown": {"source_weight": 0.0, "technical_score": 20.0},
        "compact_score": 61,
        "meta": {"metric": "华尔街见闻"},
    }
    item.update(over)
    return item


def _payload(items):
    return {
        "generated_at": "2026-10-21T00:00:00Z", "window_hours": 24,
        "topic_filter": "ai_tech_robotics",
        "total_items": len(items), "total_items_ai_raw": len(items),
        "total_items_raw": len(items), "total_items_all_mode": len(items),
        # 与 main_pipeline 一致：items 与 items_ai 是同一个列表对象
        "items": items, "items_ai": items, "items_all": list(items),
        "hot_events": [], "site_stats": [],
    }


class PayloadSlimmingTests(unittest.TestCase):
    """items 键不再重复序列化；死字段被拆掉；前端要用的字段全部保留。"""

    def test_slim_payload_has_no_duplicate_items_key(self):
        items = [_item(), _item(title="Anthropic ships Claude 5")]
        slim, all_payload = build_latest_payloads(_payload(items))
        self.assertNotIn("items", slim, "slim 不得再带与 items_ai 重复的 items 键")
        self.assertIn("items_ai", slim)
        self.assertEqual(len(slim["items_ai"]), 2)
        self.assertIn("items_all", all_payload)
        self.assertNotIn("items", all_payload)

    def test_slim_serializes_each_item_once(self):
        items = [_item()]
        slim, _ = build_latest_payloads(_payload(items))
        blob = json.dumps(slim, ensure_ascii=False)
        # 「OpenAI 发布 GPT-6」整份快照里只能出现一次
        self.assertEqual(blob.count("OpenAI 发布 GPT-6"), 1,
                         "同一标题出现多次说明 items 仍在重复序列化")

    def test_unused_fields_are_stripped(self):
        slim, all_payload = build_latest_payloads(_payload([_item()]))
        for name, payload in (("slim", slim), ("all", all_payload)):
            for item in payload["items_ai" if name == "slim" else "items_all"]:
                self.assertNotIn("signal_breakdown", item,
                                 f"{name} payload 不该带没人读的 signal_breakdown")
                self.assertNotIn("compact_score", item,
                                 f"{name} payload 不该带零消费方的 compact_score")

    def test_frontend_fields_survive_stripping(self):
        slim, _ = build_latest_payloads(_payload([_item()]))
        item = slim["items_ai"][0]
        for key in ("title", "title_zh", "url", "site_id", "site_name",
                    "source", "first_seen_at", "published_at", "category",
                    "tags", "hotness_score", "hotness_raw", "signal_score",
                    "signal_level", "recommendation_reason"):
            self.assertIn(key, item, f"前端要用的 {key} 被误删")

    def test_all_mode_payload_keeps_its_own_list(self):
        items = [_item()]
        slim, all_payload = build_latest_payloads(_payload(items))
        self.assertEqual(all_payload["total_items_all_mode"], 1)
        self.assertEqual(len(all_payload["items_all"]), 1)
        # all_payload 不该因为 slim 拆键而丢元信息
        self.assertEqual(all_payload["generated_at"], "2026-10-21T00:00:00Z")


if __name__ == "__main__":
    unittest.main()

"""编排层回归测试：Stage 顺序、提前收手、抓取预算。

背景（2026-10-20 弱点审计的三个发现，都由本文件钉住）：

1. **去重必须在翻译之前**。旧顺序是「先翻译再去重」：同一事件被 5 个来源报道
   就花 5 次翻译配额，其中 4 条会在紧接着的合并里被扔掉。每轮只有
   `translate_max_new`（默认 80）条新标题名额，被重复标题占掉的名额会把独特
   条目推迟到下一小时才拿到中文标题。`core/pipeline/main_pipeline.py` 此前
   **一行测试都没有**，这类顺序错误可以悄悄存活很久。

2. **条目数不足时要提前收手**，不能再烧一整轮翻译预算才宣布失败。

3. **OPML 抓取需要整体墙上时钟**。单 feed 有 timeout + session 级重试，最坏
   30s+；feed 数量又不设上限。预算耗尽后剩余的 feed 必须**显式记为 skipped**
   —— 静默丢弃等于把「没抓到」伪装成「没新闻」。

测试尽量走真实代码路径（真实 dedupe / 真实 Pipeline.run），只替换网络与
翻译这两个副作用点。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.models import RawItem  # noqa: E402
from core.pipeline import main_pipeline as mp  # noqa: E402

UTC = timezone.utc


def _raw(title: str, url: str, minutes_ago: int = 10) -> RawItem:
    return RawItem(
        site_id="testsrc",
        site_name="Test Source",
        source="Test",
        title=title,
        url=url,
        published_at=datetime.now(UTC) - timedelta(minutes=minutes_ago),
        meta={},
        description="desc",
    )


class _TmpDirMixin(unittest.TestCase):
    def tmpdir(self) -> Path:
        return Path(self.enterContext(tempfile.TemporaryDirectory()))


class PipelineOrderTests(_TmpDirMixin):
    """去重在翻译之前；条目不足时翻译根本不该被调用。"""

    def _run_pipeline(self, raws: list[RawItem], out_dir: Path):
        calls: list[str] = []

        def fake_collect_all(session, now):
            calls.append("fetch")
            return list(raws), []

        def fake_translate(items_ai, items_all, session, cache, max_new_translations):
            # 记录「翻译时看到的条目数」——这是本测试的核心断言
            calls.append(f"translate:{len(items_ai)}:{len(items_all)}")
            return items_ai, items_all, dict(cache)

        def fake_dedupe(items):
            calls.append(f"dedupe:{len(items)}")
            # 走真实去重逻辑，只加一行记录
            from core.dedup.deduplicator import dedupe_items_by_title_url

            return dedupe_items_by_title_url(items)

        with patch.object(mp, "collect_all", fake_collect_all), \
                patch.object(mp, "add_bilingual_fields", fake_translate), \
                patch.object(mp, "dedupe_items_by_title_url", fake_dedupe), \
                patch.object(mp, "load_archive", lambda path: {}), \
                patch.object(mp, "load_title_zh_cache", lambda path: {}), \
                patch.object(mp, "safeguard_title_zh_cache", lambda *a, **k: None), \
                patch.object(mp, "fetch_waytoagi_recent_7d",
                             lambda *a, **k: {"count_7d": 0}), \
                patch.object(mp, "maybe_send_news_notification", lambda items: True):
            pipeline = mp.Pipeline({
                "signal_score_enabled": False,
                "ai_tldr_enabled": False,
                "trend_engine_enabled": False,
            })
            result = pipeline.run(output_dir=str(out_dir))
        return result, calls

    def test_dedup_runs_before_translation(self):
        """翻译看到的必须已经是去重后的列表。"""
        out_dir = self.tmpdir()
        raws = [
            _raw("OpenAI Releases GPT-6", "https://a.test/1"),
            _raw("OpenAI Releases GPT-6", "https://a.test/1"),  # 同 URL 重复
            _raw("Anthropic Ships Claude 5", "https://b.test/2"),
            _raw("Google DeepMind Solves Protein Folding", "https://c.test/3"),
            _raw("NVIDIA Announces New AI GPU Architecture", "https://d.test/4"),
        ]
        result, calls = self._run_pipeline(raws, out_dir)

        order = [c for c in calls if c.startswith(("dedupe:", "translate:"))]
        self.assertTrue(order, "应当记录到 dedupe / translate 调用")
        self.assertTrue(
            order[0].startswith("dedupe:"),
            f"去重必须发生在翻译之前，实际顺序：{order}",
        )
        translate_calls = [c for c in order if c.startswith("translate:")]
        self.assertEqual(len(translate_calls), 1, "翻译只应被调用一次")
        # 5 条原始（含 1 条同 URL 重复）-> 去重后 4 条。
        # items_all 必须正好是 4；items_ai 由 AI 主题过滤再收一道，只会更少。
        # 关键性质：翻译看到的**不是**原始 5 条。
        ai_seen, all_seen = (int(x) for x in translate_calls[0].split(":")[1:])
        self.assertEqual(all_seen, 4, f"全量列表应是去重后的 4 条，实际 {translate_calls[0]}")
        self.assertLessEqual(ai_seen, 4, f"AI 列表不应超过去重后的条数，实际 {translate_calls[0]}")
        self.assertNotIn(5, (ai_seen, all_seen), "翻译不应收到未去重的原始条目")
        self.assertTrue(result["success"])

    def test_too_few_items_aborts_before_translation(self):
        """条目不足时直接收工，不烧翻译预算。"""
        out_dir = self.tmpdir()
        raws = [
            _raw("Only One Story", "https://a.test/1"),
            _raw("Only Two Stories", "https://b.test/2"),
        ]
        result, calls = self._run_pipeline(raws, out_dir)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "Too few AI items")
        self.assertFalse(
            any(c.startswith("translate:") for c in calls),
            f"条目不足时不该调用翻译，实际调用序列：{calls}",
        )


class OpmlBudgetTests(unittest.TestCase):
    """OPML 抓取的整体墙上时钟。"""

    OPML = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="1.0"><body>
{outlines}
</body></opml>"""

    def _write_opml(self, path: Path, count: int) -> None:
        outlines = "\n".join(
            f'<outline title="Feed {i}" xmlUrl="https://feed{i}.test/rss" htmlUrl=""/>'
            for i in range(count)
        )
        path.write_text(self.OPML.format(outlines=outlines), encoding="utf-8")

    def test_budget_exhaustion_marks_feeds_skipped_not_silently_dropped(self):
        from core.fetch.opml import fetch_opml_rss

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            opml = tmp / "subs.opml"
            self._write_opml(opml, 24)  # 20 workers -> 至少两波

            requested: list[str] = []

            class _Resp:
                status_code = 200
                content = b"<?xml version='1.0'?><rss><channel><title>t</title></channel></rss>"

                def raise_for_status(self):
                    return None

            class _Session:
                def get(self, url, timeout=None, headers=None):
                    requested.append(url)
                    # 每波 20 个 feed 并行，单波耗时 ≈ 0.25s，必然超过 0.1s 预算
                    time.sleep(0.25)
                    return _Resp()

            items, summary, statuses = fetch_opml_rss(
                datetime.now(UTC), opml, session=_Session(), budget_seconds=0.1
            )

            skipped = [s for s in statuses if s.get("skipped")]
            budget_skipped = [
                s for s in skipped if s.get("skip_reason") == "rss_budget_exhausted"
            ]
            self.assertTrue(
                budget_skipped,
                "预算耗尽必须有 feed 被记为 rss_budget_exhausted（不能静默丢弃）",
            )
            self.assertEqual(len(requested), 20, "只应抓满第一波（20 workers）")
            self.assertEqual(len(budget_skipped), 4, "剩下 4 个应被记为预算耗尽")

    def test_budget_env_parsing(self):
        from core.fetch.opml import DEFAULT_RSS_BUDGET_SECONDS, _rss_budget_seconds

        with patch.dict("os.environ", {"RSS_FETCH_BUDGET_SECONDS": "60"}):
            self.assertEqual(_rss_budget_seconds(), 60.0)
        with patch.dict("os.environ", {"RSS_FETCH_BUDGET_SECONDS": "abc"}):
            self.assertEqual(_rss_budget_seconds(), DEFAULT_RSS_BUDGET_SECONDS)
        with patch.dict("os.environ", {"RSS_FETCH_BUDGET_SECONDS": "0"}):
            self.assertEqual(_rss_budget_seconds(), DEFAULT_RSS_BUDGET_SECONDS)
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(_rss_budget_seconds(), DEFAULT_RSS_BUDGET_SECONDS)


class SnapshotConsistencyTests(_TmpDirMixin):
    """两个快照的 generated_at 必须同轮一致。"""

    def _run_validate(self, data_dir: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "validate_data.py"),
             "--data-dir", str(data_dir), "--max-age-hours", "6"],
            capture_output=True, text=True, timeout=120,
        )

    def _write_pair(self, data_dir: Path, main_ts: str, all_ts: str) -> None:
        (data_dir / "latest-24h.json").write_text(json.dumps(
            {"generated_at": main_ts, "total_items_raw": 10,
             "items": [{"title": "t", "url": "https://x.test/1"}] * 6},
        ), encoding="utf-8")
        (data_dir / "latest-24h-all.json").write_text(json.dumps(
            {"generated_at": all_ts, "items_all": [{"title": "t", "url": "https://x.test/1"}]},
        ), encoding="utf-8")

    def test_mismatched_timestamps_fail_loudly(self):
        data_dir = self.tmpdir()
        self._write_pair(data_dir, "2026-10-20T10:00:00Z", "2026-10-20T09:00:00Z")
        proc = self._run_validate(data_dir)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("快照不同步", proc.stdout + proc.stderr)

    def test_matching_timestamps_pass(self):
        data_dir = self.tmpdir()
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._write_pair(data_dir, ts, ts)
        proc = self._run_validate(data_dir)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("快照不同步", proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()

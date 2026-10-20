"""每日推送脚本的健壮性回归（对抗审查 2026-10-20 实锤）。

原始症状：`scripts/send_daily_digest.py` 的 ``load_items`` 把「读取/解析失败」
和「快照里没有条目」当成同一件事 —— 都返回空列表 → 打印「没有条目，本次不推送」
→ 退出码 0。而 `.github/workflows/daily-digest.yml` 只有 `test -f` 存在性检查，
于是一份损坏的 latest-24h.json（截断、非法 JSON）会让当日推送**静默丢失**，
工作流还是绿的。这个脚本的存在意义就是消灭「静默丢失」，结果自己在最需要的
路径上重现了它。

契约：
- 数据损坏 → 退出码 1（工作流变红，有人知道今天没推）
- 合法空快照 → 退出码 0（真的没有新闻，不推是对的）
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCRIPT = ROOT / "scripts" / "send_daily_digest.py"


class DigestSnapshotRobustnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.dir = Path(self._td.name)

    def _run(self, path: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--data", str(path)],
            capture_output=True, text=True, timeout=60,
            # 清掉 WEBHOOK_*：本测试只验证退出码语义，不真发
            env={k: v for k, v in __import__("os").environ.items()
                 if not k.startswith("WEBHOOK")},
        )

    def test_corrupt_snapshot_fails_loudly_instead_of_silent_skip(self):
        bad = self.dir / "latest-24h.json"
        bad.write_text('{"generated_at": "2026-10-20T10:00:00Z", "items_ai": [',
                       encoding="utf-8")
        proc = self._run(bad)
        self.assertEqual(
            proc.returncode, 1,
            f"损坏快照必须返回 1（当日推送丢失要可见），实际 0；stdout={proc.stdout}",
        )
        self.assertIn("无法读取", proc.stderr + proc.stdout)

    def test_non_object_snapshot_fails_loudly(self):
        bad = self.dir / "latest-24h.json"
        bad.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        proc = self._run(bad)
        self.assertEqual(proc.returncode, 1, f"stdout={proc.stdout}")

    def test_empty_snapshot_is_a_legitimate_skip(self):
        empty = self.dir / "latest-24h.json"
        empty.write_text(json.dumps({"items_ai": []}), encoding="utf-8")
        proc = self._run(empty)
        self.assertEqual(
            proc.returncode, 0,
            f"真的没有条目时不推是正确的，不该报错；stderr={proc.stderr}",
        )


if __name__ == "__main__":
    unittest.main()

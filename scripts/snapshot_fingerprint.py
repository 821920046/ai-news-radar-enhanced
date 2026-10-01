#!/usr/bin/env python
"""Verify whether the news snapshots changed in a substantive way.

The scheduled pipeline rewrites data/latest-24h.json and data/latest-24h-all.json
on every run. Both files embed a ``generated_at`` timestamp, so they always differ
by at least one byte. Committing that difference every hour produced ~1200 no-op
snapshot commits and bloated ``.git`` into the gigabyte range.

This command compares the *semantic* content of the snapshots -- with
``generated_at`` removed -- against their committed versions. A byte-identical
entry set means there is nothing worth committing, and the workflow skips the
commit entirely.

Usage:
    python scripts/snapshot_fingerprint.py --data-dir data \
        --files data/latest-24h.json data/latest-24h-all.json

Exit codes:
    0  at least one file changed substantively (or a file is new / unreadable)
    3  every checked file is substantively identical to HEAD
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

# Exit code chosen so the workflow can branch on it explicitly.
NO_SUBSTANTIVE_CHANGE = 3


def _canonical_hash(raw: str) -> str | None:
    """sha256 over the JSON with the volatile ``generated_at`` key removed.

    Returns None when ``raw`` is not a JSON object, so the caller can treat an
    unparsable snapshot as "changed" and fail safe (better to commit than to
    silently drop a real update).
    """
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    obj.pop("generated_at", None)
    canonical = json.dumps(obj, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _head_content(path: str) -> str | None:
    """Return the committed version of ``path``, or None if it is untracked."""
    proc = subprocess.run(
        ["git", "show", f"HEAD:{path}"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def main() -> int:
    ap = argparse.ArgumentParser(description="Detect substantive snapshot changes.")
    ap.add_argument("--data-dir", default=".", help="工作目录（默认当前目录）")
    ap.add_argument(
        "--files",
        nargs="+",
        required=True,
        help="要比较的文件路径（相对仓库根）",
    )
    args = ap.parse_args()

    base = Path(args.data_dir)
    changed: list[str] = []
    unchanged: list[str] = []

    for rel in args.files:
        disk = base / rel if base != Path(".") else Path(rel)
        if not disk.exists():
            # 文件消失本身就是实质变化（下游会读到空数据）
            print(f"[CHANGED] {rel}: 文件不存在")
            changed.append(rel)
            continue

        new_hash = _canonical_hash(disk.read_text(encoding="utf-8"))
        if new_hash is None:
            print(f"[CHANGED] {rel}: 不是可解析的 JSON 对象，按变化处理")
            changed.append(rel)
            continue

        head_raw = _head_content(rel)
        if head_raw is None:
            print(f"[CHANGED] {rel}: HEAD 中不存在（新文件）")
            changed.append(rel)
            continue

        old_hash = _canonical_hash(head_raw)
        if old_hash is None:
            print(f"[CHANGED] {rel}: HEAD 版本无法解析，按变化处理")
            changed.append(rel)
            continue

        if new_hash != old_hash:
            print(f"[CHANGED] {rel}")
            changed.append(rel)
        else:
            print(f"[SAME]    {rel}（仅 generated_at 不同）")
            unchanged.append(rel)

    if not changed:
        print(
            f"\n所有 {len(unchanged)} 个快照在剔除 generated_at 后与 HEAD 完全一致，"
            "跳过提交以避免仓库膨胀。"
        )
        return NO_SUBSTANTIVE_CHANGE

    print(f"\n实质变化文件: {', '.join(changed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

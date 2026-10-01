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
import re
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


#: 文本产物里的易变痕迹，逐条正则化后再比较。
#:
#: llms.txt 与 RSS 都内嵌生成时刻（llms 的「最后更新」、RSS 的 lastBuildDate），
#: 若按字节比较，它们每小时都会「变化」，于是每次运行都会产生一个提交 ——
#: 正是此前把 .git 撑到 GB 级的老问题，换了个文件重复一次。
#: 这里把这些字段替换成定值后再哈希，只在**内容真的变了**时才认为有变化。
_VOLATILE_TEXT_PATTERNS = (
    # llms.txt: 「最后更新：2026-10-01T15:31:06.833125+00:00」
    (re.compile(r"^最后更新：.*$", re.MULTILINE), "最后更新：<ts>"),
    # RSS: <lastBuildDate>Thu, 01 Oct 2026 15:31:06 +0000</lastBuildDate>
    (
        re.compile(r"<lastBuildDate>.*?</lastBuildDate>", re.DOTALL),
        "<lastBuildDate>ts</lastBuildDate>",
    ),
)


def _canonical_text_hash(raw: str) -> str:
    """对文本产物（llms.txt / RSS / robots.txt）取「剔除时间戳后」的哈希。"""
    text = raw
    for pattern, repl in _VOLATILE_TEXT_PATTERNS:
        text = pattern.sub(repl, text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hash_for(path: str, raw: str) -> tuple[str | None, bool]:
    """按扩展名分派哈希方式。

    Returns:
        (hash, is_json)。``is_json`` 为 False 表示走了文本路径。
    """
    if path.endswith(".json"):
        return _canonical_hash(raw), True
    return _canonical_text_hash(raw), False


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

        new_hash, is_json = _hash_for(rel, disk.read_text(encoding="utf-8"))
        if new_hash is None:
            print(f"[CHANGED] {rel}: 无法解析，按变化处理")
            changed.append(rel)
            continue

        head_raw = _head_content(rel)
        if head_raw is None:
            print(f"[CHANGED] {rel}: HEAD 中不存在（新文件）")
            changed.append(rel)
            continue

        old_hash, _ = _hash_for(rel, head_raw)
        if old_hash is None:
            print(f"[CHANGED] {rel}: HEAD 版本无法解析，按变化处理")
            changed.append(rel)
            continue

        if new_hash != old_hash:
            print(f"[CHANGED] {rel}")
            changed.append(rel)
        else:
            kind = "仅 generated_at 不同" if is_json else "仅时间戳不同"
            print(f"[SAME]    {rel}（{kind}）")
            unchanged.append(rel)

    if not changed:
        print(
            f"\n所有 {len(unchanged)} 个快照在剔除时间戳后与 HEAD 完全一致，"
            "跳过提交以避免仓库膨胀。"
        )
        return NO_SUBSTANTIVE_CHANGE

    print(f"\n实质变化文件: {', '.join(changed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

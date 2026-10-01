#!/usr/bin/env python
"""Data quality gate for the AI News Radar pipeline.

Runs after the pipeline writes its artifacts and before the workflow commits.
Exits non-zero when the data is missing, structurally broken, or stale, so a
silent source outage cannot be committed as a "successful" run.

Usage:
    python scripts/validate_data.py --data-dir data --max-age-hours 6
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def _load(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        print(f"[FAIL] 缺少文件: {path}")
        return {}
    except json.JSONDecodeError as exc:
        print(f"[FAIL] JSON 解析失败 {path}: {exc}")
        return {}
    if not isinstance(data, dict):
        print(f"[FAIL] 顶层结构应为对象: {path} (得到 {type(data).__name__})")
        return {}
    return data


def _age_hours(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate pipeline output artifacts.")
    ap.add_argument("--data-dir", default="data", help="数据目录（默认 data）")
    ap.add_argument("--min-items", type=int, default=3, help="最少条目数（默认 3）")
    ap.add_argument(
        "--max-age-hours",
        type=float,
        default=6.0,
        help="允许的最大数据陈旧小时数（默认 6，0 表示不检查）",
    )
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    errors: list[str] = []

    # ── 1. 主文件结构与条目数 ─────────────────────────────────────────────
    main_path = data_dir / "latest-24h.json"
    payload = _load(main_path)
    if not payload:
        errors.append(f"{main_path} 不可用")

    items = payload.get("items_ai") or payload.get("items") or payload.get("items_all") or []
    if len(items) < args.min_items:
        errors.append(f"条目数过少: {len(items)} < {args.min_items}（可能全部信源抓取失败）")

    for idx, item in enumerate(items[:5]):
        if not isinstance(item, dict):
            errors.append(f"条目[{idx}] 不是对象: {type(item).__name__}")
            continue
        if not item.get("title"):
            errors.append(f"条目[{idx}] 缺少 title")
        if not item.get("url"):
            errors.append(f"条目[{idx}] 缺少 url")

    # ── 2. 新鲜度 ─────────────────────────────────────────────────────────
    if args.max_age_hours > 0:
        age = _age_hours(payload.get("generated_at"))
        if age is None:
            errors.append("latest-24h.json 缺少可解析的 generated_at")
        elif age > args.max_age_hours:
            errors.append(
                f"数据陈旧: {age:.1f}h > {args.max_age_hours}h（generated_at={payload.get('generated_at')}）"
            )
        else:
            print(f"[OK] 数据新鲜度: {age:.2f}h")

    # ── 3. 全量文件（/hot 依赖 items_all）────────────────────────────────
    all_path = data_dir / "latest-24h-all.json"
    if all_path.exists():
        all_payload = _load(all_path)
        all_items = all_payload.get("items_all") or all_payload.get("items") or []
        if not all_items:
            errors.append(f"{all_path} 存在但没有条目（/hot 会返回 503）")
        else:
            print(f"[OK] 全量条目: {len(all_items)}")
    else:
        print(f"[WARN] 未找到 {all_path}（/hot 将回退到主文件）")

    # ── 4. 信源成功率（若存在）────────────────────────────────────────────
    status_path = data_dir / "source-status.json"
    if status_path.exists():
        status = _load(status_path)
        sources = status.get("sources") or {}
        total = len(sources)
        if total:
            ok = sum(1 for s in sources.values() if isinstance(s, dict) and s.get("ok"))
            rate = ok / total
            print(f"[INFO] 信源成功率: {ok}/{total} = {rate:.0%}")
            if rate < 0.3:
                errors.append(f"信源成功率过低: {rate:.0%} < 30%")

    # ── 汇总 ──────────────────────────────────────────────────────────────
    print(f"[INFO] 校验条目数: {len(items)}")
    if errors:
        print("\n=== 数据校验失败 ===")
        for err in errors:
            print(f"  ✗ {err}")
        return 1

    print("\n=== 数据校验通过 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())

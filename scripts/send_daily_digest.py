#!/usr/bin/env python3
"""独立发送「每日科技情报」企微推送。

为什么从主流水线里拆出来
------------------------
主流水线的每日推送靠 cron 整点命中北京时间 07:00（`WEBHOOK_MODE=daily` 的
小时门控）。Actions 排队一旦超过 1 小时，当天推送就**永久错过**——没有补发
路径，也没有任何告警，属于静默丢失。

本脚本 + `.github/workflows/daily-digest.yml` 把这件事变成一个 30 秒的小工作流
（23:30 UTC = 北京 07:30，给 23:00 那轮流水线留出提交快照的时间），基本不受
排队影响；它只读仓库里已提交的最新快照，不抓取、不翻译、不调任何 AI API。

数据来源：main 分支上的 `data/latest-24h.json`（流水线每小时提交一次）。
主流水线内的同一条推送路径已相应关闭（Update data 步骤里 `WEBHOOK_MODE=none`），
避免同一天推两次。**两个发送方必须只有一个在跑**，否则就是双推。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_items(path: Path) -> tuple[list[dict[str, Any]], str]:
    """从快照里取推送候选（与流水线用的同一批 items）。

    返回 ``(items, error)``。error 非空表示**读取/解析失败**，必须与
    「快照里没有条目」区分开：后者是合法的无推送场景（返回 0），
    前者是数据损坏 —— 当时就失败（返回 1），否则当日推送静默丢失，
    而工作流只有 test -f 存在性检查，照样是绿的。
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [], f"无法读取 {path}: {exc}"
    if not isinstance(payload, dict):
        return [], f"{path} 顶层不是 JSON 对象"
    items = payload.get("items_ai") or payload.get("items") or []
    return [i for i in items if isinstance(i, dict)], ""


def main() -> int:
    parser = argparse.ArgumentParser(description="发送每日科技情报推送")
    parser.add_argument("--data", default="data/latest-24h.json", help="快照路径")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印选材结果，不真正发送（用于本地排查）",
    )
    args = parser.parse_args()

    items, error = load_items(Path(args.data))
    if error:
        # 数据损坏不是「没有新闻」：必须显式失败，让工作流变红。
        print(f"[digest] {error}", file=sys.stderr)
        return 1
    if not items:
        # 没有候选就不推：推一条空消息比不推更糟（训练用户忽略这个频道）。
        print("[digest] 快照里没有条目，本次不推送。")
        return 0

    from core.notifier import DEFAULT_DAILY_PER_GROUP, select_daily_digest, send_daily_digest

    if args.dry_run:
        groups = select_daily_digest(items, DEFAULT_DAILY_PER_GROUP)
        for name, rows in groups:
            print(f"── {name} ──")
            for row in rows:
                print(f"   {row.get('title_zh') or row.get('title')}")
        if not any(rows for _, rows in groups):
            print("（选材为空）")
        return 0

    ok = send_daily_digest(items)
    # 退出码 1 让工作流显式变红：推送失败必须可见，不能静默。
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

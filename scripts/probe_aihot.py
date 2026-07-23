"""探针：在你自己的环境（本地/GitHub Actions）跑一下，看 AIHOT 公共 API 的真实 JSON 结构。

用法：
    python scripts/probe_aihot.py

它会打印：
  1) 顶层键（确认列表在 items / data / results 哪个键）
  2) 分页游标键（nextCursor / cursor …）
  3) 第一条条目的全部字段名（确认 title / url / source / publishedAt 的真实名字）
如果与 core/fetch/aihot_virxact.py 里的别名不一致，把真实字段名补进 _first(...) 即可。
"""

from __future__ import annotations

import json
import os

import requests

BASE = os.environ.get("AIHOT_API_BASE", "https://aihot.virxact.com/api/public").rstrip("/")


def main() -> None:
    url = f"{BASE}/items"
    resp = requests.get(
        url,
        params={"mode": os.environ.get("AIHOT_MODE", "all"), "take": 3},
        timeout=30,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json, */*"},
    )
    print("GET", resp.url, "->", resp.status_code)
    resp.raise_for_status()
    data = resp.json()

    if isinstance(data, dict):
        print("\n[顶层键]", list(data.keys()))
        print("[分页游标候选]", {k: data.get(k) for k in ("nextCursor", "next_cursor", "cursor", "next") if k in data})
        rows = None
        for k in ("items", "data", "results", "records"):
            if isinstance(data.get(k), list):
                rows = data[k]
                print(f"[列表键] {k}  (共 {len(rows)} 条)")
                break
    else:
        rows = data if isinstance(data, list) else []
        print("[顶层是数组]", len(rows), "条")

    if rows:
        print("\n[第一条条目字段]")
        print(json.dumps(rows[0], ensure_ascii=False, indent=2)[:2000])


if __name__ == "__main__":
    main()

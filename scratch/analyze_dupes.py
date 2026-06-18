#!/usr/bin/env python3
"""分析 latest-24h.json 和 latest-24h-all.json 中的重复新闻模式。"""
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def normalize_title_for_compare(title: str) -> str:
    """极简标题归一化：去除标点、空白、转小写。"""
    t = (title or "").strip().lower()
    # 去除常见前缀标记如 [讨论]、【重磅】等
    t = re.sub(r"^[\[【〖\(（].*?[\]】〗\)）]\s*", "", t)
    # 去除所有标点符号
    t = re.sub(r"[^\w\u4e00-\u9fff]+", "", t)
    return t


def analyze_file(path: Path, label: str):
    if not path.exists():
        print(f"[SKIP] {path} 不存在")
        return

    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("items", []) or data.get("items_ai", []) or data.get("items_all", [])
    print(f"\n{'='*80}")
    print(f"分析: {label} ({path.name})")
    print(f"总条目数: {len(items)}")
    print(f"{'='*80}")

    # 1. 完全相同标题
    title_counts = Counter(i.get("title", "") for i in items)
    exact_dupes = [(t, c) for t, c in title_counts.items() if c > 1]
    print(f"\n--- 完全相同标题重复: {len(exact_dupes)} 组 ---")
    for t, c in sorted(exact_dupes, key=lambda x: -x[1])[:20]:
        sources = set()
        for i in items:
            if i.get("title") == t:
                sources.add(f"{i.get('site_id','?')}:{i.get('source','?')}")
        print(f"  [{c}x] {t[:100]}")
        print(f"        来源: {', '.join(sorted(sources))}")

    # 2. 归一化后相同标题（模糊匹配）
    norm_groups = defaultdict(list)
    for i in items:
        norm = normalize_title_for_compare(i.get("title", ""))
        if norm:
            norm_groups[norm].append(i)

    fuzzy_dupes = [(k, v) for k, v in norm_groups.items() if len(v) > 1]
    print(f"\n--- 归一化后标题重复: {len(fuzzy_dupes)} 组 ---")
    for norm, group in sorted(fuzzy_dupes, key=lambda x: -len(x[1]))[:20]:
        titles = set(i.get("title", "") for i in group)
        sources = set(f"{i.get('site_id','?')}:{i.get('source','?')}" for i in group)
        print(f"  [{len(group)}x] {list(titles)[0][:100]}")
        if len(titles) > 1:
            for t in list(titles)[1:]:
                print(f"        变体: {t[:100]}")
        print(f"        来源: {', '.join(sorted(sources))}")

    # 3. 完全相同 URL
    url_counts = Counter(i.get("url", "") for i in items)
    url_dupes = [(u, c) for u, c in url_counts.items() if c > 1]
    print(f"\n--- 完全相同 URL 重复: {len(url_dupes)} 组 ---")
    for u, c in sorted(url_dupes, key=lambda x: -x[1])[:15]:
        titles = set()
        sources = set()
        for i in items:
            if i.get("url") == u:
                titles.add(i.get("title", ""))
                sources.add(f"{i.get('site_id','?')}:{i.get('source','?')}")
        print(f"  [{c}x] {u[:120]}")
        for t in sorted(titles):
            print(f"        标题: {t[:100]}")
        print(f"        来源: {', '.join(sorted(sources))}")

    # 4. 同一 URL 主域下的文章分组（跨源重复检测）
    from urllib.parse import urlparse
    domain_groups = defaultdict(list)
    for i in items:
        url = i.get("url", "")
        try:
            host = urlparse(url).netloc.lower()
            path = urlparse(url).path
            # 用 domain+path 做 key（忽略参数）
            domain_groups[f"{host}{path}"].append(i)
        except Exception:
            pass

    path_dupes = [(k, v) for k, v in domain_groups.items() if len(v) > 1]
    print(f"\n--- 同路径重复（URL path 相同，query 不同）: {len(path_dupes)} 组 ---")
    for path_key, group in sorted(path_dupes, key=lambda x: -len(x[1]))[:10]:
        print(f"  [{len(group)}x] {path_key[:120]}")
        for i in group:
            print(f"        {i.get('title','')[:80]} | {i.get('site_id')}:{i.get('source','')[:30]}")

    # 5. 分析跨源重复（同一篇新闻出现在不同 site_id 中）
    print(f"\n--- 跨源重复分析（同一标题出现在多个来源）---")
    cross_source = 0
    for norm, group in sorted(fuzzy_dupes, key=lambda x: -len(x[1])):
        site_ids = set(i.get("site_id") for i in group)
        if len(site_ids) > 1:
            cross_source += 1
            if cross_source <= 15:
                titles = set(i.get("title", "") for i in group)
                print(f"  [{len(group)}x] {list(titles)[0][:100]}")
                print(f"        跨源: {', '.join(sorted(site_ids))}")
    print(f"  共 {cross_source} 组跨源重复")

    # 6. 统计各 site_id 贡献的重复条目
    print(f"\n--- 各来源重复贡献统计 ---")
    site_total = Counter()
    site_in_dupe = Counter()
    for i in items:
        sid = i.get("site_id", "?")
        site_total[sid] += 1
    for norm, group in fuzzy_dupes:
        for i in group:
            site_in_dupe[i.get("site_id", "?")] += 1

    for sid, total in sorted(site_total.items(), key=lambda x: -x[1]):
        duped = site_in_dupe.get(sid, 0)
        pct = duped / total * 100 if total else 0
        print(f"  {sid:20s}: {total:4d} 条, {duped:3d} 条涉及重复 ({pct:.1f}%)")


if __name__ == "__main__":
    analyze_file(DATA_DIR / "latest-24h.json", "AI 筛选后（latest-24h）")
    analyze_file(DATA_DIR / "latest-24h-all.json", "全量模式（latest-24h-all）")

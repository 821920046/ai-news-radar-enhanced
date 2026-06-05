"""Deduplication and normalization of news items."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from scripts.models import UTC
from scripts.utils import event_time, normalize_url

logger = logging.getLogger(__name__)


def is_hubtoday_placeholder_title(title: str) -> bool:
    t = (title or "").strip()
    if not t:
        return True
    if "详情见官方介绍" in t:
        return True
    return t in {"原文链接", "查看详情", "点击查看", "详情"}


def is_hubtoday_generic_anchor_title(title: str) -> bool:
    t = (title or "").strip()
    if not t:
        return True
    if is_hubtoday_placeholder_title(t):
        return True
    return bool(re.search(r"\(AI资讯\)\s*$", t))


def normalize_aihubtoday_records(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_url: dict[str, list[dict[str, Any]]] = {}
    keep: list[dict[str, Any]] = []

    for item in items:
        if str(item.get("site_id") or "") != "aihubtoday":
            keep.append(item)
            continue
        url = normalize_url(str(item.get("url") or ""))
        if not url:
            continue
        by_url.setdefault(url, []).append(item)

    for group in by_url.values():
        if not group:
            continue
        preferred = [g for g in group if not is_hubtoday_generic_anchor_title(str(g.get("title") or ""))]
        source = preferred if preferred else group
        best = max(
            source,
            key=lambda x: (
                event_time(x) or datetime.min.replace(tzinfo=UTC),
                str(x.get("id") or ""),
            ),
        )
        keep.append(best)

    keep.sort(key=lambda x: event_time(x) or datetime.min.replace(tzinfo=UTC), reverse=True)
    return keep


def normalize_title_for_dedup(title: str) -> str:
    """深度标题归一化。
    1. 清理前后空白并转小写。
    2. 清除特定的前缀标记（如 【重磅】、[独家]、(AI资讯) 等）。
    3. 清除特定媒体常见的尾部后缀（如 -- 快科技、| TechCrunch 等）。
    4. 去除所有中文标点与英文标点以及所有空格，仅保留字母、数字和中文字符，达到极净状态。
    """
    t = (title or "").strip().lower()
    if not t:
        return ""

    # 1. 移除前缀
    t = re.sub(r'^(?:【[^】]+】|\[[^\]]+\]|\([^)]+\))\s*', '', t)

    # 2. 移除一些已知的常见特殊长后缀
    t = re.sub(r'\s*--\s*(?:快科技|科技改变未来|36氪|网易|新浪|腾讯|搜狐).*$', '', t)

    # 3. 移除分隔符后的短媒体后缀
    parts = re.split(r'\s*(?:\||-|_)\s*', t)
    if len(parts) > 1:
        last_part = parts[-1].strip()
        if last_part and (
            len(last_part) < 15 and (
                re.search(r'(?:科技|新闻|网|资讯|频道|社区|论坛|博客|app|weekly|daily|blog|news|tech|group|ltd|量子位|见闻|钛媒体|虎嗅|氪|快科技|快报|爱范儿|之家|机器之心|极客|品玩)$', last_part)
                or re.match(r'^[a-z0-9\s]+$', last_part)
            )
        ):
            t = t.rsplit(parts[-1], 1)[0].strip().rstrip('|-_\t ')

    # 4. 去除标点符号与所有空格，仅保留中文字符、英文字母和数字
    t = "".join(re.findall(r'[\w\u4e00-\u9fff]+', t))
    return t


def compute_title_similarity(a: str, b: str) -> float:
    """计算两个标题的 bigram Jaccard 相似度。
    输入应当是经过清洗后的标题（例如由 normalize_title_for_dedup 处理后的极净字符串）。
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0

    # 快速过滤：如果长度差异过大（例如较短者长度不足较长者的 60%），直接判定不相似
    len_a, len_b = len(a), len(b)
    if min(len_a, len_b) / max(len_a, len_b) < 0.60:
        return 0.0

    # 生成 bigram 集合
    def get_bigrams(s: str) -> set[str]:
        if len(s) < 2:
            return set(s)
        return {s[i:i+2] for i in range(len(s) - 1)}

    set_a = get_bigrams(a)
    set_b = get_bigrams(b)

    union = len(set_a.union(set_b))
    if union == 0:
        return 0.0
    return len(set_a.intersection(set_b)) / union


def _pick_best_item(group: list[dict[str, Any]]) -> dict[str, Any]:
    """在一组重复/相似的新闻条目中挑选出信息最丰富、来源最优、发布最早的最佳条目。"""
    if not group:
        raise ValueError("group cannot be empty")
    if len(group) == 1:
        return group[0]

    # 来源优先级（信号源等级越高，权重分越高）
    site_priority = {
        "official_ai": 100,
        "opmlrss": 90,
        "buzzing": 80,
        "zeli": 70,
        "tophub": 60,
        "newsnow": 50,
        "iris": 40
    }

    def get_priority(item: dict[str, Any]) -> int:
        sid = str(item.get("site_id") or "").strip().lower()
        return site_priority.get(sid, 0)

    def get_hotness(item: dict[str, Any]) -> float:
        try:
            return float(item.get("hotness_score") or 0.0)
        except (ValueError, TypeError):
            return 0.0

    def is_placeholder_title(t: str) -> bool:
        t_strip = t.strip()
        if not t_strip:
            return True
        if "详情见官方介绍" in t_strip:
            return True
        return t_strip in {"原文链接", "查看详情", "点击查看", "详情"} or bool(re.search(r"\(AI资讯\)\s*$", t_strip))

    def has_valid_title(item: dict[str, Any]) -> int:
        title = str(item.get("title_original") or item.get("title") or "")
        return 0 if is_placeholder_title(title) else 1

    def has_valid_desc(item: dict[str, Any]) -> int:
        desc = str(item.get("description") or "").strip()
        if not desc:
            return 0
        if desc in {"原文链接", "查看详情", "点击查看", "详情"} or "详情见官方介绍" in desc:
            return 0
        return 1

    def get_timestamp(item: dict[str, Any]) -> float:
        # 发布时间越晚，时间戳越大，排序越优先
        dt = event_time(item)
        if not dt:
            return 0.0  # 无时间的垫底
        return dt.timestamp()

    best = max(
        group,
        key=lambda x: (
            has_valid_title(x),
            get_priority(x),
            has_valid_desc(x),
            get_hotness(x),
            get_timestamp(x),
            str(x.get("id") or ""),
        )
    )
    return best


def merge_items_group(group: list[dict[str, Any]]) -> dict[str, Any]:
    """将一组相似/重复的新闻记录合并为单一优质记录，整合多源元数据。"""
    if not group:
        raise ValueError("group cannot be empty")
    if len(group) == 1:
        return group[0]

    # 1. 选取代表性的最佳条目
    best = _pick_best_item(group)
    merged = dict(best)

    # 2. 合并 tags 并去重
    merged_tags = set()
    for item in group:
        for t in (item.get("tags") or []):
            if t:
                merged_tags.add(str(t).strip())
    merged["tags"] = sorted(list(merged_tags))

    # 3. 收集并合并所有的来源信息，防止重复
    all_sources = []
    seen_sources = set()
    for item in group:
        sid = str(item.get("site_id") or "").strip()
        sname = str(item.get("site_name") or "").strip()
        src = str(item.get("source") or "").strip()
        url = str(item.get("url") or "").strip()

        # 合并已有的合并来源（如果有的话）
        inner_sources = item.get("merged_sources")
        if isinstance(inner_sources, list):
            for inner in inner_sources:
                i_sid = str(inner.get("site_id") or "").strip()
                i_src = str(inner.get("source") or "").strip()
                source_key = (i_sid.lower(), i_src.lower())
                if source_key not in seen_sources:
                    seen_sources.add(source_key)
                    all_sources.append(inner)
        else:
            source_key = (sid.lower(), src.lower())
            if source_key not in seen_sources:
                seen_sources.add(source_key)
                all_sources.append({
                    "site_id": sid,
                    "site_name": sname,
                    "source": src,
                    "url": url
                })

    merged["merged_sources"] = all_sources
    source_count = len(all_sources)
    merged["source_count"] = source_count

    # 4. 综合热度加权计算
    max_hotness = 0.0
    for item in group:
        try:
            val = float(item.get("hotness_score") or 0.0)
            if val > max_hotness:
                max_hotness = val
        except (ValueError, TypeError):
            pass

    if source_count > 1:
        merged["hotness_score"] = max_hotness + (source_count - 1) * 15.0
        merged["hotness_raw"] = f"多源聚合 x{source_count}"
    else:
        merged["hotness_score"] = max_hotness

    return merged


def dedupe_items_by_title_url(items: list[dict[str, Any]], similarity_threshold: float = 0.70, **kwargs: Any) -> list[dict[str, Any]]:
    """使用多层级联算法（URL 精确匹配 -> 标题精确匹配 -> 标题相似度模糊匹配）对新闻记录进行强力去重。
    
    Parameters
    ----------
    items : list[dict]
        待去重的新闻列表。
    similarity_threshold : float
        模糊标题相似度匹配阈值，默认为 0.70。
    **kwargs :
        向后兼容参数。
    """
    if not items:
        return []

    # --- Layer 1: 基于归一化 URL 精确匹配合并 ---
    by_url: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        url = normalize_url(str(item.get("url") or ""))
        by_url.setdefault(url, []).append(item)

    after_url_dedup: list[dict[str, Any]] = []
    for group in by_url.values():
        after_url_dedup.append(merge_items_group(group))

    # --- Layer 2: 基于极净标题精确匹配合并 ---
    by_clean_title: dict[str, list[dict[str, Any]]] = {}
    for item in after_url_dedup:
        title_original = item.get("title_original") or item.get("title") or ""
        clean_title = normalize_title_for_dedup(title_original)
        if not clean_title:
            clean_title = f"__empty_title__::{normalize_url(str(item.get('url') or ''))}"
        by_clean_title.setdefault(clean_title, []).append(item)

    after_title_dedup: list[dict[str, Any]] = []
    for group in by_clean_title.values():
        after_title_dedup.append(merge_items_group(group))

    # --- Layer 3: 基于标题 Bigram Jaccard 相似度模糊合并 ---
    final_items: list[dict[str, Any]] = []
    for item in after_title_dedup:
        matched = False
        title_original = item.get("title_original") or item.get("title") or ""
        item_clean_title = normalize_title_for_dedup(title_original)

        for existing in final_items:
            existing_title_original = existing.get("title_original") or existing.get("title") or ""
            existing_clean_title = normalize_title_for_dedup(existing_title_original)

            if not item_clean_title or not existing_clean_title:
                continue

            # 快速过滤：长度差异过大不进行计算
            len_a, len_b = len(item_clean_title), len(existing_clean_title)
            if min(len_a, len_b) / max(len_a, len_b) < 0.60:
                continue

            sim = compute_title_similarity(item_clean_title, existing_clean_title)
            if sim >= similarity_threshold:
                # 模糊匹配成功，原地更新 existing 以进行合并
                merged_result = merge_items_group([existing, item])
                existing.clear()
                existing.update(merged_result)
                matched = True
                break

        if not matched:
            final_items.append(item)

    # 按发布时间/事件时间排序（最新的排在前面）
    final_items.sort(key=lambda x: event_time(x) or datetime.min.replace(tzinfo=UTC), reverse=True)
    return final_items

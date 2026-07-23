"""Deduplication and normalization of news items.

本模块负责把多来源采集到的新闻做强力去重合并。相比早期版本，本轮在
“相同/高度雷同内容仍重复出现”上做了下列优化（均向后兼容，不改变已有公开函数的行为）：

1. Layer 1 URL 合并改用更强的 `_canonical_dedup_url`：
   同一篇文章的 http/https、www./m./amp. 前缀、尾部 /amp 与 index.html
   等等价写法现在会被归一为同一键。
2. Layer 3 模糊层新增“包含关系（containment）”与“描述佐证”两条召回路径，
   保证副标题/同款改写标题、跨源同文也能合并。
3. Layer 3 用 bigram 倒排索引做候选块（blocking），把原本 O(n^2) 的两两比对
   降为“只与共享字片的少量候选”比对，在大数据量下显著提速且不降低召回。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse, urlunparse

from core.models import UTC
from core.utils import event_time, normalize_url

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


def _title_bigrams(s: str) -> set[str]:
    """生成标题的 bigram 集合（长度 <2 时退化为单字集合）。"""
    if len(s) < 2:
        return set(s)
    return {s[i:i + 2] for i in range(len(s) - 1)}


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

    set_a = _title_bigrams(a)
    set_b = _title_bigrams(b)

    union = len(set_a.union(set_b))
    if union == 0:
        return 0.0
    return len(set_a.intersection(set_b)) / union


def compute_containment(a: str, b: str) -> float:
    """计算“较短标题的 bigram 有多大比例被较长标题覆盖”（包含度）。
    用于捕捉“标题A” vs “标题A（附视频/实测）”这类子集/超集标题，
    这种情形下 Jaccard 会因长度差异而偏低。
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    set_a = _title_bigrams(a)
    set_b = _title_bigrams(b)
    short, long = (set_a, set_b) if len(set_a) <= len(set_b) else (set_b, set_a)
    if not short:
        return 0.0
    return len(short & long) / len(short)


def _raw_bigram_jaccard(a: str, b: str) -> float:
    """不做长度剪枝的 bigram Jaccard，用于描述佐证等内部判定。"""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    set_a = _title_bigrams(a)
    set_b = _title_bigrams(b)
    union = len(set_a | set_b)
    return len(set_a & set_b) / union if union else 0.0


def _clean_text_for_dedup(text: Any) -> str:
    """正文/描述的极净归一化（仅留中英文与数字），用于描述佐证合并。"""
    t = str(text or "").strip().lower()
    if not t:
        return ""
    return "".join(re.findall(r'[\w\u4e00-\u9fff]+', t))


# 模糊层参数（可调）
_CONTAINMENT_THRESHOLD = 0.90   # 包含度达到此值视为子集/超集重复（较短标题几乎完全被较长标题覆盖）
_CONTAINMENT_MIN_LEN = 8        # 参与包含判定的较短标题最小字符数（避免短/通用标题误合）
_CONTAINMENT_MIN_RATIO = 0.45   # 较短/较长标题长度比下限（避免把短标题并入超长无关标题）
_DESC_CORROBORATE_MIN_SIM = 0.50  # 描述佐证时标题需达到的最低相似度
_DESC_CORROBORATE_MIN_LEN = 20    # 描述佐证需要的最小描述字符数
_DESC_CORROBORATE_SIM = 0.80      # 描述相似度达到此值视为同一内容


def _titles_are_duplicate(
    item_clean_title: str,
    existing_clean_title: str,
    item: dict[str, Any],
    existing: dict[str, Any],
    similarity_threshold: float,
) -> bool:
    """多信号判定两条新闻是否为重复：
    1) bigram Jaccard 相似度 >= 阈值（主路径，与旧版一致）；
    2) 子集/超集标题（包含度 >= 0.90 且长度比 >= 0.45，较短标题 >= 8 字）；
    3) 标题中度相似（>=0.5）且描述高度一致（>=0.8）——跨源同文改写标题。
    """
    sim = compute_title_similarity(item_clean_title, existing_clean_title)
    if sim >= similarity_threshold:
        return True

    # 包含关系（子集/超集标题）：不依赖会做长度剪枝的 compute_title_similarity，
    # 因为本分支正是为了处理“标题A” vs “标题A（附��…）”这种长度差异大的情形。
    short_len = min(len(item_clean_title), len(existing_clean_title))
    long_len = max(len(item_clean_title), len(existing_clean_title))
    if (
        short_len >= _CONTAINMENT_MIN_LEN
        and long_len > 0
        and short_len / long_len >= _CONTAINMENT_MIN_RATIO
        and compute_containment(item_clean_title, existing_clean_title) >= _CONTAINMENT_THRESHOLD
    ):
        return True

    # 描述佐证的灰区合并：标题中度相似 + 描述高度一致 => 跨源同文改写标题
    if sim >= _DESC_CORROBORATE_MIN_SIM:
        d1 = _clean_text_for_dedup(item.get("description"))
        d2 = _clean_text_for_dedup(existing.get("description"))
        if (
            len(d1) >= _DESC_CORROBORATE_MIN_LEN
            and len(d2) >= _DESC_CORROBORATE_MIN_LEN
            and _raw_bigram_jaccard(d1, d2) >= _DESC_CORROBORATE_SIM
        ):
            return True

    return False


def _canonical_dedup_url(raw_url: str) -> str:
    """更激进的 URL 归一化（仅用于去重键，不用于展示）。

    在 normalize_url（去 utm/ref 等追踪参数）基础上额外：
    - 统一 scheme 为 https（同一文章 http/https 视为同源）
    - 去掉 www. / m. / amp. / mobile. 等等价子域前缀
    - 去掉路径尾部的 /amp、index.html、default.html 与多余斜杠
    """
    base = normalize_url(str(raw_url or ""))
    if not base:
        return ""
    try:
        parsed = urlparse(base)
        if not parsed.scheme:
            return base
        netloc = parsed.netloc.lower()
        for prefix in ("www.", "m.", "amp.", "mobile."):
            if netloc.startswith(prefix):
                netloc = netloc[len(prefix):]
                break
        path = parsed.path or ""
        path = re.sub(r'/(?:amp|index\.html?|default\.html?)/?$', '', path, flags=re.I)
        path = path.rstrip('/')
        parsed = parsed._replace(scheme="https", netloc=netloc, path=path, fragment="")
        return urlunparse(parsed).rstrip("/")
    except Exception:
        return base


def _pick_best_item(group: list[dict[str, Any]]) -> dict[str, Any]:
    """在一组重复/相似的新闻条目中挑选出信息最丰富、来源最优、发布最早的最佳条目。"""
    if not group:
        raise ValueError("group cannot be empty")
    if len(group) == 1:
        return group[0]

    # 来源优先级（信号源等级越高，权重分越高）
    site_priority = {
        "official_ai": 100,
        "oss_trending": 95,
        "opmlrss": 90,
        "aihot": 88,
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
    # 使用更强的 canonical URL（http/https、www./m./amp.、尾部 amp/index 均归一为同一键）
    by_url: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        url = _canonical_dedup_url(str(item.get("url") or ""))
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
            clean_title = f"__empty_title__::{_canonical_dedup_url(str(item.get('url') or ''))}"
        by_clean_title.setdefault(clean_title, []).append(item)

    after_title_dedup: list[dict[str, Any]] = []
    for group in by_clean_title.values():
        after_title_dedup.append(merge_items_group(group))

    # --- Layer 3: 基于标题 Bigram Jaccard 相似度 + 包含度 + 描述佐证的模糊合并 ---
    # 性能优化：用 bigram 倒排索引做候选块（blocking），只与“共享至少一个
    # bigram”的已有条目比对，把 O(n^2) 的全量两两比对降为近线性。
    # 因为任意两条真正相似/包含的标题必然共享大量 bigram，故不会降低召回。
    final_items: list[dict[str, Any]] = []
    final_clean_titles: list[str] = []
    bigram_index: dict[str, set[int]] = {}

    def _register(idx: int, clean_title: str) -> None:
        for bg in _title_bigrams(clean_title):
            bigram_index.setdefault(bg, set()).add(idx)

    for item in after_title_dedup:
        matched = False
        title_original = item.get("title_original") or item.get("title") or ""
        item_clean_title = normalize_title_for_dedup(title_original)

        if item_clean_title:
            # 从倒排索引收集候选（共享至少一个 bigram 的已有条目）
            candidates: set[int] = set()
            for bg in _title_bigrams(item_clean_title):
                candidates |= bigram_index.get(bg, set())

            for idx in sorted(candidates):
                existing = final_items[idx]
                existing_clean_title = final_clean_titles[idx]
                if not existing_clean_title:
                    continue
                if _titles_are_duplicate(
                    item_clean_title, existing_clean_title, item, existing, similarity_threshold
                ):
                    # 模糊匹配成功，原地更新 existing 以进行合并
                    merged_result = merge_items_group([existing, item])
                    existing.clear()
                    existing.update(merged_result)
                    # 合并后代表标题可能变化，刷新缓存与倒排索引
                    merged_title = existing.get("title_original") or existing.get("title") or ""
                    new_clean = normalize_title_for_dedup(merged_title)
                    if new_clean != existing_clean_title:
                        final_clean_titles[idx] = new_clean
                        _register(idx, new_clean)
                    matched = True
                    break

        if not matched:
            new_idx = len(final_items)
            final_items.append(item)
            final_clean_titles.append(item_clean_title)
            if item_clean_title:
                _register(new_idx, item_clean_title)

    # 按发布时间/事件时间排序（最新的排在前面）
    final_items.sort(key=lambda x: event_time(x) or datetime.min.replace(tzinfo=UTC), reverse=True)
    return final_items

"""Feature extractors for Signal Score 2.0 — 从文章集合中提取多维信号特征。

每个函数返回 0-100 的归一化分数，供 SignalScoreEngine 使用。
"""

from __future__ import annotations

import logging
import math
import re
from datetime import timedelta
from typing import Any

from core.utils import event_time, strip_html_tags, utc_now

logger = logging.getLogger(__name__)


def compute_velocity(articles: list[dict], current_article: dict) -> float:
    """计算同一 24h 窗口内有多少文章讨论相似话题。返回 0-100。

    通过倒排索引预召回和提前剪枝，极大优化了海量比对时的算法性能，彻底避免超时。
    """
    article_time = event_time(current_article)
    if not article_time:
        return 0.0

    if not articles or len(articles) <= 1:
        return 0.0

    now = utc_now()
    window_start = now - timedelta(hours=24)

    # 1. 尝试从第一篇文章字典获取当前批次的倒排索引和映射缓存
    cache_holder = articles[0]
    cache = cache_holder.get("_velocity_cache_data")
    
    if cache is None:
        inverted = {}
        id_map = {}
        for art in articles:
            art_id = id(art)
            id_map[art_id] = art
            
            # 预处理标题词
            art_title_words = art.get("_title_words")
            if art_title_words is None:
                art_title = strip_html_tags(str(art.get("title") or "")).lower()
                art_title_words = {w for w in re.findall(r"[a-zA-Z一-鿿]{2,}", art_title)}
                art["_title_words"] = art_title_words
            
            for w in art_title_words:
                if w not in inverted:
                    inverted[w] = []
                inverted[w].append(art_id)

            # 预处理标签为倒排索引词
            art_tags = art.get("tags") or []
            for t in art_tags:
                tag_key = f"tag:{t}"
                if tag_key not in inverted:
                    inverted[tag_key] = []
                inverted[tag_key].append(art_id)
                
        cache = {"inverted": inverted, "id_map": id_map}
        cache_holder["_velocity_cache_data"] = cache
    else:
        inverted = cache["inverted"]
        id_map = cache["id_map"]

    # 2. 提取当前文章的特征集
    current_tags = set(current_article.get("tags") or [])
    
    current_title_words = current_article.get("_title_words")
    if current_title_words is None:
        current_title = strip_html_tags(str(current_article.get("title") or "")).lower()
        current_title_words = {w for w in re.findall(r"[a-zA-Z一-鿿]{2,}", current_title)}
        current_article["_title_words"] = current_title_words

    current_bigrams = current_article.get("_desc_bigrams")
    if current_bigrams is None:
        current_desc = strip_html_tags(str(current_article.get("description") or "")).lower()
        if len(current_desc) > 20:
            current_bigrams = {current_desc[i : i + 2] for i in range(len(current_desc) - 1)}
        else:
            current_bigrams = set()
        current_article["_desc_bigrams"] = current_bigrams

    # 3. 通过倒排索引找出潜在相关的候选文章 ID，避开 99% 不相关的新闻
    candidate_ids = set()
    for w in current_title_words:
        if w in inverted:
            candidate_ids.update(inverted[w])
    for t in current_tags:
        tag_key = f"tag:{t}"
        if tag_key in inverted:
            candidate_ids.update(inverted[tag_key])

    # 排除自身
    candidate_ids.discard(id(current_article))

    # 4. 针对候选集进行精确比对，达到匹配阈值时提前终止循环
    related_count = 0
    for art_id in candidate_ids:
        article = id_map.get(art_id)
        if not article:
            continue

        art_time = event_time(article)
        if not art_time or art_time < window_start:
            continue

        # 标签重叠
        art_tags = set(article.get("tags") or [])
        if current_tags and art_tags and (current_tags & art_tags):
            related_count += 1
            if related_count >= 5:
                return 100.0
            continue

        # 关键词重叠（至少 2 个共同标题词）
        art_title_words = article.get("_title_words")
        if len(current_title_words & art_title_words) >= 2:
            related_count += 1
            if related_count >= 5:
                return 100.0
            continue

        # 描述文本重叠 (Bigrams)
        art_bigrams = article.get("_desc_bigrams")
        if art_bigrams is None:
            art_desc = strip_html_tags(str(article.get("description") or "")).lower()
            if len(art_desc) > 20:
                art_bigrams = {art_desc[i : i + 2] for i in range(len(art_desc) - 1)}
            else:
                art_bigrams = set()
            article["_desc_bigrams"] = art_bigrams

        if current_bigrams and art_bigrams and len(current_bigrams & art_bigrams) >= 3:
            related_count += 1
            if related_count >= 5:
                return 100.0

    return min(100.0, related_count * 20.0)


def compute_novelty(article: dict, archive: dict | None = None) -> float:
    """计算文章的新颖度。返回 0-100。

    基于：
    - first_seen_at 的新近程度（6h 内最高，24h 内次之）
    - 官方信源 + 近期发布的加成
    - computer_hardware / digital 类文章扣分（AI 相关度较低）
    - 与历史存档标题的相似度对比（如果提供了 archive）
    """
    score = 0.0

    # 1. 发布时间新近度
    first_seen = event_time(article)
    if first_seen:
        now = utc_now()
        age_hours = (now - first_seen).total_seconds() / 3600.0
        if age_hours <= 6:
            score += 40.0
        elif age_hours <= 24:
            score += 20.0

    # 2. 官方信源 + 近期发布加成
    site_id = str(article.get("site_id") or "")
    if site_id == "official_ai":
        if first_seen:
            age_hours = (utc_now() - first_seen).total_seconds() / 3600.0
            if age_hours <= 48:
                score += 30.0
            elif age_hours <= 168:  # within a week
                score += 15.0
        else:
            score += 10.0

    # 3. 非 AI 类别文章扣分
    category = str(article.get("category") or "").lower()
    tags = [str(t).lower() for t in (article.get("tags") or [])]
    all_text = f"{category} {' '.join(tags)}"
    if any(kw in all_text for kw in ("computer_hardware", "digital", "硬件", "数码")):
        score -= 20.0

    # 4. 与历史存档对比（如果提供）
    if archive and first_seen:
        current_title = strip_html_tags(str(article.get("title") or "")).lower()
        current_url = str(article.get("url") or "").strip()

        similar_count = 0
        for arch_id, arch_item in archive.items():
            arch_title = strip_html_tags(str(arch_item.get("title") or "")).lower()
            arch_url = str(arch_item.get("url") or "").strip()

            # 完全相同的 URL 或标题 → 不新鲜
            if current_url and arch_url and current_url == arch_url:
                score -= 15.0
                similar_count += 1
                continue
            if current_title and arch_title and current_title == arch_title:
                score -= 15.0
                similar_count += 1
                continue

            # 标题近似（80% 以上重叠的大词）
            current_words = set(re.findall(r"[a-zA-Z一-鿿]{2,}", current_title))
            arch_words = set(re.findall(r"[a-zA-Z一-鿿]{2,}", arch_title))
            if current_words and arch_words:
                overlap = current_words & arch_words
                overlap_ratio = len(overlap) / max(len(current_words), len(arch_words), 1)
                if overlap_ratio > 0.8:
                    score -= 5.0
                    similar_count += 1

            if similar_count >= 5:
                break

    return max(0.0, min(100.0, score))


def compute_community_signal(article: dict) -> float:
    """计算社区参与信号。返回 0-100。

    基于：
    - merged_sources 的数量（多信源合并 = 高参与）
    - OSS trending 的 GitHub stars（对数归一）
    - recommendation_reason 中是否包含具体实体
    """
    score = 0.0

    # 1. 合并信源数量
    merged_sources = article.get("merged_sources")
    if isinstance(merged_sources, list) and len(merged_sources) > 1:
        score += 40.0

    # 2. OSS trending 星标信号
    site_id = str(article.get("site_id") or "")
    if site_id == "oss_trending":
        meta = article.get("meta") or {}
        if isinstance(meta, dict):
            total_stars = 0.0
            try:
                total_stars = float(meta.get("total_stars") or 0)
            except (TypeError, ValueError):
                pass
            if total_stars > 0:
                score += min(100.0, math.log10(max(1, total_stars)) * 20.0)

    # 3. recommendation_reason 包含具体实体
    reason = str(article.get("recommendation_reason") or "")
    # 实体关键词：产品名、公司名、模型名等
    entity_kw = re.findall(
        r"OpenAI|Anthropic|DeepMind|Google|Microsoft|Meta|NVIDIA|"
        r"DeepSeek|Qwen|Gemini|GPT|Claude|Llama|Mistral|Cursor|"
        r"HuggingFace|Midjourney|Runway|Perplexity|"
        r"阿里|通义|百度|文心|字节|豆包|腾讯|混元",
        reason,
        re.I,
    )
    if entity_kw:
        score += min(20.0, len(set(e.lower() for e in entity_kw)) * 5.0)

    return max(0.0, min(100.0, score))

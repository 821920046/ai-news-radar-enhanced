"""趋势聚类：通过 OpenRouter embeddings + 余弦相似度将新闻文章聚类为话题。

核心流程：
1. 为每篇文章拼接 title_zh + title + description + tldr 作为嵌入文本
2. 调用 OpenRouter /api/v1/embeddings 获取向量（成本控制：每轮最多 embed N 篇）
3. 贪心聚类：每篇向量与已有簇中心计算余弦相似度，>= 阈值则归入该簇并更新中心点
4. 无法嵌入或 API 不可用时回退到标签聚类
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

# OpenRouter embeddings endpoint
OPENROUTER_EMBEDDINGS_URL = "https://openrouter.ai/api/v1/embeddings"
DEFAULT_EMBEDDING_MODEL = "openai/text-embedding-3-small"
# 每轮最多 embed 的文章数（成本控制）
DEFAULT_MAX_EMBEDDINGS = 50
# 余弦相似度阈值，超过此值归入同一簇
DEFAULT_SIMILARITY_THRESHOLD = 0.85


class TrendClustering:
    """使用语义嵌入向量对新闻文章进行话题聚类。"""

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.similarity_threshold = self.config.get(
            "similarity_threshold", DEFAULT_SIMILARITY_THRESHOLD
        )
        self.max_embeddings = self.config.get("max_embeddings", DEFAULT_MAX_EMBEDDINGS)
        self.embedding_model = self.config.get("embedding_model", DEFAULT_EMBEDDING_MODEL)
        # 单次批量 embedding 的最大文本数（成本/请求数控制）
        self.batch_size = int(self.config.get("embedding_batch_size", 96))

    def get_embedding(
        self,
        text: str,
        api_key: str,
        *,
        session: requests.Session | None = None,
        timeout: int = 15,
        referer: str = "https://github.com/LearnPrompt/ai-news-radar",
    ) -> list[float] | None:
        """调用 OpenRouter embeddings API 获取文本向量，失败返回 None。"""
        if not text or not text.strip():
            return None

        requester = session or requests
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": referer,
            "X-Title": "AI News Radar Trend Engine",
        }
        payload = {
            "model": self.embedding_model,
            "input": text[:8000],  # 截断避免 token 超限
        }

        try:
            response = requester.post(
                OPENROUTER_EMBEDDINGS_URL,
                headers=headers,
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict) and "data" in data:
                embedding = data["data"][0].get("embedding")
                if isinstance(embedding, list) and embedding:
                    return embedding
        except Exception as exc:
            logger.warning("OpenRouter embedding failed: %s", exc)

        return None

    def get_embeddings_batch(
        self,
        texts: list[str],
        api_key: str,
        *,
        session: requests.Session | None = None,
        timeout: int = 30,
        referer: str = "https://github.com/LearnPrompt/ai-news-radar",
    ) -> list[list[float] | None]:
        """批量获取文本向量。

        一次请求提交多条文本（OpenRouter embeddings 的 input 支持数组），
        将原来 N 次调用压缩为 ceil(N/batch_size) 次，大幅减少请求数。
        返回与输入等长且位置对齐的列表；某项失败则该位置为 None。
        整批请求失败时，对该批逐条回退到 get_embedding。
        """
        results: list[list[float] | None] = [None] * len(texts)
        if not texts:
            return results

        requester = session or requests
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": referer,
            "X-Title": "AI News Radar Trend Engine",
        }
        batch_size = max(1, int(self.batch_size))

        for start in range(0, len(texts), batch_size):
            chunk = texts[start : start + batch_size]
            # 仅对非空文本请求，同时记录其在 chunk 内的局部下标
            valid = [(i, t) for i, t in enumerate(chunk) if t and t.strip()]
            if not valid:
                continue

            payload = {
                "model": self.embedding_model,
                "input": [t[:8000] for _, t in valid],
            }
            try:
                response = requester.post(
                    OPENROUTER_EMBEDDINGS_URL,
                    headers=headers,
                    json=payload,
                    timeout=timeout,
                )
                response.raise_for_status()
                data = response.json()
                items = data.get("data") if isinstance(data, dict) else None
                if not isinstance(items, list) or not items:
                    raise ValueError("unexpected embeddings response shape")
                # 优先按返回的 index 对齐，缺失时退回顺序
                for pos, emb_obj in enumerate(items):
                    if isinstance(emb_obj, dict):
                        local = emb_obj.get("index", pos)
                        emb = emb_obj.get("embedding")
                    else:
                        local, emb = pos, None
                    if (
                        isinstance(emb, list)
                        and emb
                        and isinstance(local, int)
                        and 0 <= local < len(valid)
                    ):
                        results[start + valid[local][0]] = emb
                time.sleep(0.05)  # API 速率限制
            except Exception as exc:
                logger.warning(
                    "OpenRouter batch embedding failed (%d-%d): %s; 逐条回退。",
                    start,
                    start + len(chunk),
                    exc,
                )
                for local_i, t in valid:
                    emb = self.get_embedding(
                        t, api_key, session=session, timeout=timeout, referer=referer
                    )
                    if emb:
                        results[start + local_i] = emb
                    time.sleep(0.05)

        return results

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        """纯 Python 余弦相似度计算，不依赖 numpy。"""
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = (sum(x * x for x in a)) ** 0.5
        norm_b = (sum(x * x for x in b)) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def cluster(
        self, articles: list[dict], api_key: str | None = None
    ) -> list[dict]:
        """对文章列表进行聚类，返回话题簇列表。

        每个簇 dict 包含：
        - topic: str       话题名称（来自高频标签或标题）
        - center: list[float]  簇中心向量
        - items: list[dict]    该簇包含的文章
        - size: int            文章数量

        无 API key 或无文章时返回空列表或标签回退结果。
        """
        if not articles:
            return []

        # 特性开关：需要 API key
        key = api_key or os.environ.get("OPENROUTER_KEYS", "").split(",")[0].strip()
        if not key:
            logger.info("[TrendEngine] No OpenRouter key; falling back to tag clustering.")
            return self._fallback_tag_clustering(articles)

        # 为前 N 篇文章生成嵌入向量（成本控制）——批量请求，大幅减少 API 调用次数
        to_embed = articles[: self.max_embeddings]
        texts = [self._article_text(article) for article in to_embed]
        vectors = self.get_embeddings_batch(texts, key)
        embeddings_map: dict[int, list[float]] = {
            idx: vec for idx, vec in enumerate(vectors) if vec
        }

        if not embeddings_map:
            logger.info("[TrendEngine] No embeddings generated; falling back to tag clustering.")
            return self._fallback_tag_clustering(articles)

        # 贪心聚类：每篇文章与已有簇中心计算余弦相似度
        clusters: list[dict] = []
        for idx, emb in embeddings_map.items():
            matched = False
            for cluster in clusters:
                sim = self._cosine_similarity(emb, cluster["center"])
                if sim >= self.similarity_threshold:
                    cluster["items"].append(to_embed[idx])
                    # 更新簇中心为所有成员向量的均值
                    n = len(cluster["items"])
                    cluster["center"] = [
                        (cluster["center"][j] * (n - 1) + emb[j]) / n
                        for j in range(len(emb))
                    ]
                    matched = True
                    break

            if not matched:
                clusters.append(
                    {
                        "center": emb,
                        "items": [to_embed[idx]],
                    }
                )

        # 为每个簇生成话题名称
        for cluster in clusters:
            cluster["topic"] = self._name_cluster(cluster["items"])
            cluster["size"] = len(cluster["items"])

        # 按簇大小降序排列
        clusters.sort(key=lambda c: c["size"], reverse=True)
        return clusters

    def _article_text(self, article: dict) -> str:
        """从文章 dict 中提取用于嵌入的代表性文本。"""
        parts = [
            article.get("title_zh", ""),
            article.get("title", ""),
            article.get("description", ""),
            article.get("tldr", ""),
        ]
        return " ".join(str(p).strip() for p in parts if p and str(p).strip())

    def _name_cluster(self, items: list[dict]) -> str:
        """从簇内文章的标签中选出最高频的 1-2 个作为话题名称。"""
        tag_counts: dict[str, int] = {}
        for item in items:
            for tag in item.get("tags", []):
                tag = str(tag).strip()
                if tag:
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1

        if tag_counts:
            top_tags = sorted(tag_counts, key=tag_counts.get, reverse=True)[:2]
            return " · ".join(top_tags)

        # 回退：用第一篇文章的标题作为话题名
        if items:
            title = items[0].get("title_zh") or items[0].get("title", "Unknown")
            return str(title)[:40]
        return "Unknown Topic"

    def _fallback_tag_clustering(self, articles: list[dict]) -> list[dict]:
        """无嵌入时的回退方案：按文章标签（取第一个标签）聚类。"""
        clusters: dict[str, list[dict]] = {}
        for article in articles:
            tags = article.get("tags", [])
            if not tags:
                key = "未分类"
            else:
                key = str(tags[0]).strip()
            clusters.setdefault(key, []).append(article)

        return [
            {"topic": topic, "items": items, "size": len(items), "center": []}
            for topic, items in sorted(
                clusters.items(), key=lambda x: len(x[1]), reverse=True
            )
        ]

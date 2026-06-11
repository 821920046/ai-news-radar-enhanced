"""开源热榜: GitHub Trending + Vercel 模板市场每日热门开源项目抓取器。

从 GitHub Trending 和 Vercel 模板市场抓取热门开源项目，综合热度排名后取 TOP 30。
两个来源各自独立抓取，异常互不影响，有任意一个成功即视为整体 OK。
"""

from __future__ import annotations

import logging
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

from scripts.models import (
    BROWSER_UA,
    GITHUB_TRENDING_URL,
    OSS_MAX_COMBINED,
    RawItem,
    VERCEL_AI_SDK_TOPIC_URL,
    VERCEL_TEMPLATES_URL,
)
from scripts.utils import maybe_fix_mojibake

logger = logging.getLogger(__name__)

_SITE_ID = "oss_trending"
_SITE_NAME = "开源热榜"

# GitHub Trending 页面每个仓库行的选择器（BeautifulSoup）
_GH_ARTICLE_SELECTORS = [
    "article.Box-row",
    ".Box article.Box-row",
    ".Box-row",
]
# 仓库名: owner / name 格式
_GH_REPO_NAME_RE = re.compile(r"^\s*([\w._-]+)\s*/\s*([\w._-]+)\s*$")

# 数字解析: "1,234" / "12.5k" / "1.2万"
_NUM_RE = re.compile(r"[\d,]+\.?\d*")
_MULTIPLIER_MAP = {"k": 1_000, "K": 1_000, "万": 10_000, "M": 1_000_000, "m": 1_000_000}


def _parse_metric(raw: str | None) -> float:
    """将 '1,234', '12.5k', '1.2万' 等字符串转为数字。"""
    if not raw or not raw.strip():
        return 0.0
    s = raw.strip().replace(",", "").replace(" ", "")
    if not s:
        return 0.0
    multiplier = 1
    for suffix, mult in _MULTIPLIER_MAP.items():
        if s.endswith(suffix):
            s = s[:-len(suffix)]
            multiplier = mult
            break
    try:
        return float(s) * multiplier
    except ValueError:
        return 0.0


# ──────────────────────────────────────────────────────────────────────
# 子抓取 1: GitHub Trending (每日热门仓库)
# ──────────────────────────────────────────────────────────────────────

def _fetch_github_trending(session: requests.Session, now: datetime) -> list[RawItem]:
    """抓取 github.com/trending?since=daily，提取每日热门仓库 TOP 30。

    提取信息：仓库名、描述、编程语言、今日星标、总星标、复刻数。
    热门页面本身已按今日星标排序，直接取前 30 个有效条目。
    """
    out: list[RawItem] = []
    try:
        resp = session.get(
            GITHUB_TRENDING_URL,
            timeout=25,
            headers={
                "User-Agent": BROWSER_UA,
                "Accept": "text/html,application/xhtml+xml,*/*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("GitHub Trending 请求失败: %s", exc)
        return out

    soup = BeautifulSoup(resp.text, "html.parser")
    articles = soup.select("article.Box-row")
    if not articles:
        logger.warning("GitHub Trending 未找到 article.Box-row 元素")
        return out

    for article in articles[:OSS_MAX_COMBINED]:
        try:
            # ── 仓库名: h2.h3.lh-condensed > a[href="/owner/repo"] ──
            h2 = article.select_one("h2")
            repo_link = h2.select_one("a") if h2 else None
            if not repo_link:
                continue
            href = str(repo_link.get("href", "")).strip()
            # href 格式: /apple/container
            parts = [p for p in href.split("/") if p]
            if len(parts) < 2:
                continue
            owner, name = parts[0], parts[1]
            repo_full = f"{owner}/{name}"
            url = f"https://github.com/{repo_full}"

            # ── 描述: p.col-9.color-fg-muted ──
            desc_el = article.select_one("p.col-9")
            description = desc_el.get_text(" ", strip=True) if desc_el else ""

            # ── 编程语言: span[itemprop="programmingLanguage"] ──
            lang_el = article.select_one('span[itemprop="programmingLanguage"]')
            language = lang_el.get_text(" ", strip=True) if lang_el else ""

            # ── 今日星标: span.float-sm-right (内含 "X,XXX stars today") ──
            stars_today = 0.0
            today_el = article.select_one("span.float-sm-right, span.d-inline-block.float-sm-right")
            if today_el:
                today_text = today_el.get_text(" ", strip=True)
                today_match = re.search(r"([\d,]+\.?\d*)\s*stars?\s*today", today_text, re.IGNORECASE)
                if today_match:
                    stars_today = _parse_metric(today_match.group(1))

            # ── 总星标: 第一个 a[href*='/stargazers'] 链接（不含 "today" 的纯数字） ──
            total_stars = 0.0
            stargazer_links = article.select("a[href*='/stargazers']")
            for sl in stargazer_links:
                st_text = sl.get_text(" ", strip=True)
                # 跳过 Star 按钮、含 "today" 的行
                if "today" in st_text.lower() or "star" in st_text.lower():
                    continue
                val = _parse_metric(st_text)
                if val > 0:
                    total_stars = val
                    break

            # ── 复刻数: a[href*='/forks'] ──
            forks = 0.0
            fork_link = article.select_one("a[href*='/forks']")
            if fork_link:
                fork_text = fork_link.get_text(" ", strip=True)
                forks = _parse_metric(fork_text)

            title = maybe_fix_mojibake(repo_full)
            meta: dict[str, Any] = {
                "platform": "github_trending",
                "language": language,
                "stars_today": stars_today,
                "total_stars": total_stars,
                "forks": forks,
            }

            out.append(
                RawItem(
                    site_id=_SITE_ID,
                    site_name=_SITE_NAME,
                    source="GitHub Trending",
                    title=title,
                    url=url,
                    published_at=now,
                    meta=meta,
                    description=description,
                )
            )
        except Exception as exc:
            logger.debug("GitHub Trending 单条解析失败: %s", exc)
            continue

    if not out:
        logger.warning("GitHub Trending 未解析到任何有效项目")

    return out


# ──────────────────────────────────────────────────────────────────────
# 子抓取 2: Vercel 模板市场 / GitHub topics
# ──────────────────────────────────────────────────────────────────────

def _fetch_vercel_trending(session: requests.Session, now: datetime) -> list[RawItem]:
    """抓取 Vercel 模板市场热门模板。

    主路径：vercel.com/templates —— 提取模板名称、描述、框架标签。
    备用路径：github.com/topics/vercel-ai-sdk —— 包含 vercel-ai-sdk topic 的热门仓库。
    """
    out: list[RawItem] = []

    # ── 路径 A: Vercel 模板市场 ──
    try:
        resp = session.get(
            VERCEL_TEMPLATES_URL,
            timeout=25,
            headers={
                "User-Agent": BROWSER_UA,
                "Accept": "text/html,application/xhtml+xml,*/*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("Vercel 模板市场请求失败: %s", exc)
        resp = None

    if resp and resp.status_code == 200:
        soup = BeautifulSoup(resp.text, "html.parser")
        # Vercel 模板市场常见选择器（可能随页面结构变化而需要调整）
        cards = soup.select(
            "a[href^='/templates/'], "
            "[class*='template'] a, "
            "[class*='TemplateCard'] a, "
            "a[href*='/templates/']"
        )
        if not cards:
            # 宽泛回退：任何包含链接的卡片样式元素
            cards = soup.select("[class*='card'] a[href*='template'], [class*='Card'] a[href*='template']")

        seen_urls: set[str] = set()
        for card in cards[:OSS_MAX_COMBINED]:
            try:
                href = str(card.get("href", "")).strip()
                if not href:
                    continue
                url = urljoin("https://vercel.com", href)
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                # 模板名称
                title_el = card.select_one("h2, h3, h4, [class*='title'], [class*='name'], strong")
                title = title_el.get_text(" ", strip=True) if title_el else card.get_text(" ", strip=True)[:120]
                title = maybe_fix_mojibake(title)
                if not title or len(title) < 2:
                    continue

                # 描述 —— 找卡片内描述文字
                desc_el = card.select_one("p, [class*='desc'], [class*='description']")
                description = desc_el.get_text(" ", strip=True) if desc_el else ""

                # 框架标签 —— 卡片内常见 tech badge
                framework = ""
                badge_els = card.select("[class*='badge'], [class*='tag'], [class*='pill'], span.text-xs")
                if badge_els:
                    frameworks = [b.get_text(" ", strip=True) for b in badge_els[:3] if b.get_text(" ", strip=True)]
                    framework = " · ".join(frameworks)

                out.append(
                    RawItem(
                        site_id=_SITE_ID,
                        site_name=_SITE_NAME,
                        source="Vercel 生态",
                        title=title,
                        url=url,
                        published_at=now,
                        meta={
                            "platform": "vercel_ecosystem",
                            "framework": framework,
                            "stars_today": 0,
                            "total_stars": 0,
                            "forks": 0,
                        },
                        description=description,
                    )
                )
            except Exception as exc:
                logger.debug("Vercel 模板单条解析失败: %s", exc)
                continue

    if out:
        logger.info("Vercel 模板市场抓取到 %d 条", len(out))
        return out

    # ── 路径 B: GitHub topics/vercel-ai-sdk 备用 ──
    logger.info("Vercel 模板市场为空，尝试 GitHub topics/vercel-ai-sdk 备用路径")
    try:
        resp = session.get(
            VERCEL_AI_SDK_TOPIC_URL,
            timeout=25,
            headers={
                "User-Agent": BROWSER_UA,
                "Accept": "text/html,application/xhtml+xml,*/*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("GitHub topics/vercel-ai-sdk 请求失败: %s", exc)
        return out

    soup = BeautifulSoup(resp.text, "html.parser")
    repo_articles = soup.select("article.border, article.Box-row, [class*='repo'] article, .d-flex.width-full")

    for article in repo_articles[:OSS_MAX_COMBINED]:
        try:
            link_el = article.select_one("a[data-view-component='true'], a.text-bold, h3 a")
            if not link_el:
                continue
            href = str(link_el.get("href", "")).strip()
            if not href.startswith("/") or href.count("/") < 2:
                continue
            owner, name = href.strip("/").split("/")[:2]
            repo_full = f"{owner}/{name}"
            title = maybe_fix_mojibake(repo_full)
            url = f"https://github.com/{repo_full}"

            desc_el = article.select_one("p, [class*='description']")
            description = desc_el.get_text(" ", strip=True) if desc_el else ""

            # 星标
            stars_el = article.select_one("a[href*='stargazers'], .mr-3 + a")
            stars_text = stars_el.get_text(" ", strip=True) if stars_el else ""
            stars = _parse_metric(stars_text)

            out.append(
                RawItem(
                    site_id=_SITE_ID,
                    site_name=_SITE_NAME,
                    source="Vercel 生态",
                    title=title,
                    url=url,
                    published_at=now,
                    meta={
                        "platform": "vercel_ecosystem",
                        "framework": "AI SDK",
                        "stars_today": 0,
                        "total_stars": stars,
                        "forks": 0,
                    },
                    description=description,
                )
            )
        except Exception as exc:
            logger.debug("GitHub topic 单条解析失败: %s", exc)
            continue

    if not out:
        logger.warning("Vercel 生态两个路径均未获取到有效项目")
    else:
        logger.info("GitHub topics/vercel-ai-sdk 备用路径抓取到 %d 条", len(out))
    return out


# ──────────────────────────────────────────────────────────────────────
# 编排器
# ──────────────────────────────────────────────────────────────────────

def fetch_oss_trending(session: requests.Session, now: datetime) -> list[RawItem]:
    """抓取开源热榜：GitHub Trending + Vercel 模板市场，综合排名取 TOP 30。

    两个子抓取并发执行，各自异常独立捕获。任意一个成功即返回有效数据。
    """
    all_items: list[RawItem] = []

    tasks = [
        ("GitHub Trending", lambda: _fetch_github_trending(session, now)),
        ("Vercel 生态", lambda: _fetch_vercel_trending(session, now)),
    ]

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_map: dict[Any, str] = {executor.submit(fn): name for name, fn in tasks}
        for future in as_completed(future_map):
            name = future_map[future]
            try:
                items = future.result()
                all_items.extend(items)
            except Exception as exc:
                logger.warning("开源热榜子源 %s 失败: %s", name, exc)

    if not all_items:
        raise ValueError("开源热榜两个子源均未返回有效项目")

    # ├─ 综合排序：按热度分数降序排列
    # │  热度分数公式：log10(stars_today+1)*250 + log10(total_stars+1)*30
    # │  Vercel 模板无星标数据，固定 400 分
    # └─ 排序后截取 TOP N
    def _score(item: RawItem) -> float:
        meta = item.meta or {}
        platform = str(meta.get("platform") or "")
        if platform == "github_trending":
            stars_today = float(meta.get("stars_today") or 0)
            total_stars = float(meta.get("total_stars") or 0)
            s = math.log10(max(1, stars_today) + 1) * 250 + math.log10(max(1, total_stars) + 1) * 30
            return min(1000, max(300, s))
        elif platform == "vercel_ecosystem":
            framework = str(meta.get("framework") or "")
            return 420 if framework else 400
        return 300

    all_items.sort(key=_score, reverse=True)
    result = all_items[:OSS_MAX_COMBINED]

    logger.info("开源热榜共抓取 %d 条，综合排名后取 TOP %d 条", len(all_items), len(result))
    return result

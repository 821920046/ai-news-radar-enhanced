"""AI News Radar V3 — FastAPI 应用入口（优化版）。

优化点（相对旧版）：
- JSON 文件读取加内存缓存 + mtime 失效，避免每次请求重复磁盘 IO。
- CORS 收紧：默认不再 allow_credentials；允许来源可通过环境变量 ALLOW_ORIGINS 配置。
- 修复弃用的 datetime.utcnow() -> datetime.now(timezone.utc)。
- /health 返回数据新鲜度（age_hours / stale）与源成功率。
- /trends 优先读取趋势引擎产出的 data/trends.json，回退到按 tag 计数，并标注 source。
- 读取失败有兜底，不会因坏文件 500。

部署:
    uvicorn api.app:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

logger = logging.getLogger(__name__)

VERSION = "3.1.0"
STALE_HOURS = float(os.getenv("DATA_STALE_HOURS", "6"))

app = FastAPI(
    title="AI News Radar V3",
    description="AI 情报系统 API — 提供每日 AI 新闻、趋势分析和行业日报",
    version=VERSION,
)

# CORS — 来源可由环境变量 ALLOW_ORIGINS（逗号分隔）配置；默认 "*" 但不带凭证
_origins = [o.strip() for o in os.getenv("ALLOW_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

# ── 简易内存限流 + 安全/缓存响应头中间件（零依赖，适合免费部署）──
_RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_MIN", "120"))
_RATE_WINDOW = 60.0
_RATE_BUCKET: dict[str, list[float]] = {}
_RATE_LOCK = Lock()
_PUBLIC_CACHE_SECONDS = int(os.getenv("PUBLIC_CACHE_SECONDS", "300"))
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Cross-Origin-Resource-Policy": "cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


@app.middleware("http")
async def _security_and_rate_limit(request: Request, call_next):
    # 按客户端 IP 的滑动窗口限流（本地内存，进程级）
    if _RATE_LIMIT > 0:
        client = request.client.host if request.client else "unknown"
        now = time.monotonic()
        with _RATE_LOCK:
            bucket = _RATE_BUCKET.setdefault(client, [])
            cutoff = now - _RATE_WINDOW
            bucket[:] = [t for t in bucket if t > cutoff]
            if len(bucket) >= _RATE_LIMIT:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too Many Requests"},
                    headers={"Retry-After": "60"},
                )
            bucket.append(now)
    response = await call_next(request)
    for _k, _v in _SECURITY_HEADERS.items():
        response.headers.setdefault(_k, _v)
    if request.method == "GET" and response.status_code == 200:
        response.headers.setdefault(
            "Cache-Control", f"public, max-age={_PUBLIC_CACHE_SECONDS}"
        )
    return response

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# ── 带 mtime 失效的内存缓存 ─────────────────────────────────────────────────
_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_LOCK = Lock()


def _load_json(path: Path) -> dict:
    """读取 JSON，带 mtime 失效缓存。文件缺失/损坏返回空 dict。"""
    if not path.exists():
        return {}
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}

    key = str(path)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and cached[0] == mtime:
            return cached[1]

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("读取 %s 失败", path)
        return {}

    with _CACHE_LOCK:
        _CACHE[key] = (mtime, data)
    return data


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hours_since(iso_str: str | None) -> float | None:
    if not iso_str:
        return None
    try:
        ts = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0


def _items_of(payload: dict) -> list[dict]:
    return payload.get("items_ai", payload.get("items", [])) or []


# ── Routes ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    """健康检查：含数据新鲜度与源成功率。"""
    payload = _load_json(DATA_DIR / "latest-24h.json")
    status = _load_json(DATA_DIR / "source-status.json")
    age = _hours_since(payload.get("generated_at"))
    return {
        "status": "ok",
        "version": VERSION,
        "timestamp": _now_iso(),
        "data_generated_at": payload.get("generated_at"),
        "data_age_hours": round(age, 2) if age is not None else None,
        "stale": bool(age is not None and age > STALE_HOURS),
        "total_items": payload.get("total_items", 0),
        "successful_sources": status.get("successful_sites", 0),
        "failed_sources": status.get("failed_sites", []),
    }


@app.get("/daily-report")
def daily_report():
    """返回最新的 24h AI 日报（JSON 格式）。"""
    payload = _load_json(DATA_DIR / "latest-24h.json")
    if not payload:
        raise HTTPException(status_code=503, detail="暂无数据，请等待 Pipeline 生成")
    return JSONResponse(content=payload)


@app.get("/daily-report/markdown")
def daily_report_markdown():
    """返回 Markdown 格式的 AI 日报文本。"""
    payload = _load_json(DATA_DIR / "latest-24h.json")
    if not payload:
        return PlainTextResponse(
            "# AI News Radar\n\n暂无数据，请等待 Pipeline 生成。", media_type="text/markdown"
        )

    items = _items_of(payload)
    lines = [
        "# 🧠 AI Industry Daily Report",
        f"**{payload.get('generated_at', 'N/A')}** | 共 {len(items)} 条\n",
        "## ⚡ 今日要点\n",
    ]
    for item in items[:15]:
        title = item.get("title_zh") or item.get("title", "Unknown")
        score = item.get("signal_score", "-")
        tldr = item.get("tldr", "")
        url = item.get("url", "#")
        source = item.get("source", item.get("site_name", ""))
        level = item.get("signal_level", "")
        level_icon = {"S": "🔴", "A": "🟠", "B": "🟡"}.get(level, "")
        rec = item.get("recommendation_reason", "")

        lines.append(f"### {level_icon} [{level}] {title}")
        if tldr:
            lines.append(f"> {tldr}")
        lines.append(f"来源: {source} | 信号分: {score}")
        if rec:
            lines.append(f"💡 {rec}")
        lines.append(f"[阅读原文]({url})\n")

    lines.append(f"\n---\n_AI News Radar V3 · 自动生成 · {len(items)} 条 AI 新闻_")
    return PlainTextResponse("\n".join(lines), media_type="text/markdown")


@app.get("/trends")
def trends():
    """返回趋势话题。优先趋势引擎产出，回退到按 tag 计数。"""
    engine_out = _load_json(DATA_DIR / "trends.json")
    if engine_out.get("trends"):
        engine_out.setdefault("source", "trend_engine")
        return engine_out

    payload = _load_json(DATA_DIR / "latest-24h.json")
    if not payload:
        raise HTTPException(status_code=503, detail="暂无数据")

    items = _items_of(payload)
    tag_counts: dict[str, int] = {}
    tag_items: dict[str, list[dict]] = {}
    for item in items:
        for tag in item.get("tags", []):
            tag = str(tag).strip()
            if not tag:
                continue
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
            tag_items.setdefault(tag, []).append({
                "title": item.get("title_zh") or item.get("title"),
                "url": item.get("url"),
                "signal_score": item.get("signal_score", 0),
                "source": item.get("source", item.get("site_name", "")),
            })

    trend_list = sorted(
        [{"tag": tag, "count": count, "items": tag_items.get(tag, [])[:5]}
         for tag, count in tag_counts.items()],
        key=lambda x: x["count"], reverse=True,
    )
    return {
        "generated_at": payload.get("generated_at"),
        "total_items": len(items),
        "trends": trend_list,
        "trend_count": len(trend_list),
        "source": "tag_fallback",
    }


@app.get("/items")
def get_items(
    limit: int = Query(20, ge=1, le=200, description="返回条数"),
    min_score: float = Query(0, ge=0, le=100, description="最低信号分"),
    level: str = Query("", description="过滤等级: S/A/B/C"),
    tag: str = Query("", description="按标签过滤"),
    offset: int = Query(0, ge=0, description="偏移量"),
):
    """查询新闻条目，支持分页、评分过滤和标签过滤。"""
    payload = _load_json(DATA_DIR / "latest-24h.json")
    if not payload:
        raise HTTPException(status_code=503, detail="暂无数据")

    items = _items_of(payload)
    if min_score > 0:
        items = [i for i in items if float(i.get("signal_score", 0)) >= min_score]
    if level:
        items = [i for i in items if i.get("signal_level", "").upper() == level.upper()]
    if tag:
        items = [i for i in items if tag in [str(t).strip() for t in i.get("tags", [])]]

    total = len(items)
    page = items[offset:offset + limit]
    return {"total": total, "limit": limit, "offset": offset, "items": page}


@app.get("/stats")
def stats():
    """返回数据统计摘要。"""
    payload = _load_json(DATA_DIR / "latest-24h.json")
    status = _load_json(DATA_DIR / "source-status.json")
    age = _hours_since(payload.get("generated_at"))
    return {
        "generated_at": payload.get("generated_at"),
        "data_age_hours": round(age, 2) if age is not None else None,
        "total_items": payload.get("total_items", 0),
        "total_ai_items": payload.get("total_items", 0),
        "total_raw_items": payload.get("total_items_raw", 0),
        "site_count": payload.get("site_count", 0),
        "source_count": payload.get("source_count", 0),
        "successful_sources": status.get("successful_sites", 0),
        "failed_sources": status.get("failed_sites", []),
        "archive_total": payload.get("archive_total", 0),
    }


def _is_opensource(item: dict) -> bool:
    """判断一条资讯是否为开源项目。"""
    if item.get("is_opensource") is True:
        return True
    cat = str(item.get("category") or item.get("type") or "").lower()
    if any(k in cat for k in ("open", "repo", "github")):
        return True
    src = str(item.get("source") or item.get("site_name") or "").lower()
    if "github" in src or "trending" in src:
        return True
    tags = ",".join(str(t).lower() for t in item.get("tags", []))
    if any(k in tags for k in ("开源", "github", "trending", "repo")):
        return True
    if item.get("stars") is not None or item.get("star_count") is not None:
        return True
    return False


def _hotness(item: dict) -> float:
    """热度：优先 hotness/stars，其次 signal_score。"""
    for key in ("hotness", "stars", "star_count"):
        if item.get(key) is not None:
            try:
                return float(item[key])
            except (TypeError, ValueError):
                pass
    try:
        return float(item.get("signal_score", 0))
    except (TypeError, ValueError):
        return 0.0


def _slim(item: dict) -> dict:
    """精简输出，仅保留前端热榜所需字段。"""
    return {
        "title": item.get("title"),
        "title_zh": item.get("title_zh"),
        "url": item.get("url") or item.get("link"),
        "source": item.get("source") or item.get("site_name"),
        "signal_score": item.get("signal_score"),
        "signal_level": item.get("signal_level"),
        "hotness": _hotness(item),
        "stars": item.get("stars") or item.get("star_count"),
        "tldr": item.get("tldr") or item.get("summary"),
        "recommendation_reason": item.get("recommendation_reason"),
        "tags": item.get("tags", []),
        "published": item.get("published") or item.get("published_at") or item.get("date"),
        "is_opensource": _is_opensource(item),
    }


@app.get("/hot")
def hot(top: int = Query(20, ge=1, le=100, description="每个榜单返回条数")):
    """24 小时热榜：拆分「热门新闻」与「热门开源」两个榜单。

    优先使用全量数据（latest-24h-all.json）以便覆盖非 AI 强相关的开源热榜，
    回退到 latest-24h.json。按热度降序。
    """
    payload = _load_json(DATA_DIR / "latest-24h-all.json") or _load_json(
        DATA_DIR / "latest-24h.json"
    )
    items = _items_of(payload)
    if not items:
        raise HTTPException(status_code=503, detail="暂无数据")

    os_items, news_items = [], []
    for it in items:
        (os_items if _is_opensource(it) else news_items).append(it)

    news_items.sort(key=_hotness, reverse=True)
    os_items.sort(key=_hotness, reverse=True)
    return {
        "generated_at": payload.get("generated_at"),
        "news": [_slim(i) for i in news_items[:top]],
        "opensource": [_slim(i) for i in os_items[:top]],
        "news_total": len(news_items),
        "opensource_total": len(os_items),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

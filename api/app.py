"""AI News Radar V3 — FastAPI 应用入口。

部署:
    uvicorn api.app:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

logger = logging.getLogger(__name__)

app = FastAPI(
    title="AI News Radar V3",
    description="AI 情报系统 API — 提供每日 AI 新闻、趋势分析和行业日报",
    version="3.0.0",
)

# CORS — 允许前端跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


# ── Routes ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    """健康检查端点。"""
    return {"status": "ok", "version": "3.0.0", "timestamp": datetime.utcnow().isoformat()}


@app.get("/daily-report")
def daily_report():
    """返回最新的 24h AI 日报（JSON 格式）。"""
    path = DATA_DIR / "latest-24h.json"
    if not path.exists():
        raise HTTPException(status_code=503, detail="暂无数据，请等待 Pipeline 生成")
    payload = _load_json(path)
    return JSONResponse(content=payload)


@app.get("/daily-report/markdown")
def daily_report_markdown():
    """返回 Markdown 格式的 AI 日报文本。"""
    path = DATA_DIR / "latest-24h.json"
    if not path.exists():
        return PlainTextResponse("# AI News Radar\n\n暂无数据，请等待 Pipeline 生成。", media_type="text/markdown")

    payload = _load_json(path)
    items = payload.get("items_ai", payload.get("items", []))

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
    """返回当前检测到的趋势和突发话题。"""
    path = DATA_DIR / "latest-24h.json"
    if not path.exists():
        raise HTTPException(status_code=503, detail="暂无数据")

    payload = _load_json(path)
    items = payload.get("items_ai", payload.get("items", []))

    # 简单趋势推导：按 tag 聚合
    tag_counts: dict[str, int] = {}
    tag_items: dict[str, list[dict]] = {}
    for item in items:
        for tag in item.get("tags", []):
            tag = str(tag).strip()
            if tag:
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
    path = DATA_DIR / "latest-24h.json"
    if not path.exists():
        raise HTTPException(status_code=503, detail="暂无数据")

    payload = _load_json(path)
    items = payload.get("items_ai", payload.get("items", []))

    # 过滤
    if min_score > 0:
        items = [i for i in items if float(i.get("signal_score", 0)) >= min_score]
    if level:
        items = [i for i in items if i.get("signal_level", "").upper() == level.upper()]
    if tag:
        items = [i for i in items if tag in [str(t).strip() for t in i.get("tags", [])]]

    total = len(items)
    page = items[offset:offset + limit]

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": page,
    }


@app.get("/stats")
def stats():
    """返回数据统计摘要。"""
    path = DATA_DIR / "latest-24h.json"
    status_path = DATA_DIR / "source-status.json"

    payload = _load_json(path)
    status = _load_json(status_path)

    return {
        "generated_at": payload.get("generated_at"),
        "total_items": payload.get("total_items", 0),
        "total_ai_items": payload.get("total_items", 0),
        "total_raw_items": payload.get("total_items_raw", 0),
        "site_count": payload.get("site_count", 0),
        "source_count": payload.get("source_count", 0),
        "successful_sources": status.get("successful_sites", 0),
        "failed_sources": status.get("failed_sites", []),
        "archive_total": payload.get("archive_total", 0),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

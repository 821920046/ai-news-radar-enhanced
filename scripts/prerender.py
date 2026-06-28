#!/usr/bin/env python3
"""构建期静态预渲染（SSG）——解决纯客户端 SPA 的首屏白屏与 SEO 问题。

读取 data/latest-24h.json，把前 N 条新闻渲染进 index.html：
  1. 注入真实 <title>/description/Open Graph/Twitter Card + JSON-LD ItemList（SEO 可收录）
  2. 内联首屏数据为 <script id="__PRERENDER_DATA__" type="application/json">，
     前端可优先读取它而非再发一次 fetch（去掉首屏一跳）
  3. 在 #prerender-root 容器输出服务端渲染的新闻列表，首屏无需等待 JS

幂等：通过 HTML 注释标记定位注入区，可被每小时 CI 重复调用而不会累积垃圾。
首次运行若模板里没有标记，会在 </head> 前与 <body> 后自动插入标记。

前端配合（可选但推荐）：
  - JS 初始化时先尝试读取 #__PRERENDER_DATA__ 的 JSON 作为首屏数据；
  - hydrate 完成后再清空/覆盖 #prerender-root。

用法：
    python scripts/prerender.py --data data/latest-24h.json --html index.html --top 30
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("prerender")

HEAD_MARK = ("<!-- PRERENDER:HEAD -->", "<!-- /PRERENDER:HEAD -->")
DATA_MARK = ("<!-- PRERENDER:DATA -->", "<!-- /PRERENDER:DATA -->")
ITEMS_MARK = ("<!-- PRERENDER:ITEMS -->", "<!-- /PRERENDER:ITEMS -->")

SITE_NAME = "AI Signal Board"
SITE_DESC_FALLBACK = "24 小时 AI/科技情报雷达 — 多源聚合、智能评分、趋势检测。"


def _esc(text) -> str:
    return html.escape(str(text if text is not None else ""), quote=True)


def _wrap(marker: tuple[str, str], content: str) -> str:
    start, end = marker
    return f"{start}\n{content}\n{end}"


def ensure_markers(doc: str) -> str:
    """确保三组标记存在；缺失则在合适锚点插入空标记区。"""
    if HEAD_MARK[0] not in doc:
        block = _wrap(HEAD_MARK, "")
        if "</head>" in doc:
            doc = doc.replace("</head>", block + "\n</head>", 1)
        else:
            doc = block + "\n" + doc

    inject = ""
    if DATA_MARK[0] not in doc:
        inject += _wrap(DATA_MARK, "") + "\n"
    if ITEMS_MARK[0] not in doc:
        inject += '<div id="prerender-root">\n' + _wrap(ITEMS_MARK, "") + "\n</div>\n"
    if inject:
        m = re.search(r"<body[^>]*>", doc, re.I)
        if m:
            idx = m.end()
            doc = doc[:idx] + "\n" + inject + doc[idx:]
        else:
            doc = inject + doc
    return doc


def fill(doc: str, marker: tuple[str, str], content: str) -> str:
    start, end = marker
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
    return pattern.sub(lambda _m: _wrap(marker, content), doc, count=1)


def strip_seo(doc: str) -> str:
    """移除可能重复的 SEO 标签（description/og/twitter/JSON-LD），随后由本脚本统一重建。"""
    patterns = [
        r'<meta[^>]+name=["\']description["\'][^>]*>',
        r'<meta[^>]+property=["\']og:[^"\']+["\'][^>]*>',
        r'<meta[^>]+name=["\']twitter:[^"\']+["\'][^>]*>',
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>.*?</script>',
    ]
    for p in patterns:
        doc = re.sub(p, "", doc, flags=re.S | re.I)
    return doc


def update_title(doc: str, title: str) -> str:
    new = f"<title>{_esc(title)}</title>"
    if re.search(r"<title>.*?</title>", doc, re.S | re.I):
        return re.sub(r"<title>.*?</title>", lambda _m: new, doc, count=1, flags=re.S | re.I)
    if "</head>" in doc:
        return doc.replace("</head>", new + "\n</head>", 1)
    return new + doc


def build_head(items: list[dict], generated_at: str, og_image: str = "", base_url: str = "") -> str:
    desc_titles = [(it.get("title_zh") or it.get("title") or "") for it in items[:5]]
    desc = " / ".join(t for t in desc_titles if t) or SITE_DESC_FALLBACK
    desc = desc[:200]
    top_title = (items[0].get("title_zh") or items[0].get("title")) if items else ""
    title_tag = f"{SITE_NAME} — {top_title}" if top_title else f"{SITE_NAME} — 24 小时 AI 资讯雷达"

    elements = [
        {
            "@type": "ListItem",
            "position": i,
            "url": it.get("url"),
            "name": it.get("title_zh") or it.get("title"),
        }
        for i, it in enumerate(items[:30], 1)
    ]
    jsonld = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": f"{SITE_NAME} — 24h AI 新闻",
        "dateModified": generated_at,
        "itemListElement": elements,
    }

    lines = [
        f'<meta name="description" content="{_esc(desc)}">',
        '<meta property="og:type" content="website">',
        f'<meta property="og:site_name" content="{_esc(SITE_NAME)}">',
        f'<meta property="og:title" content="{_esc(title_tag)}">',
        f'<meta property="og:description" content="{_esc(desc)}">',
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{_esc(title_tag)}">',
        f'<meta name="twitter:description" content="{_esc(desc)}">',
    ]
    if base_url:
        lines.insert(0, f'<link rel="canonical" href="{base_url}/">')
        lines.append(f'<meta property="og:url" content="{base_url}/">')
    if og_image:
        lines.append(f'<meta property="og:image" content="{_esc(og_image)}">')
        lines.append(f'<meta name="twitter:image" content="{_esc(og_image)}">')
    lines.append(
        '<script type="application/ld+json">'
        + json.dumps(jsonld, ensure_ascii=False)
        + "</script>"
    )
    return "\n".join(lines)


def build_items(items: list[dict]) -> str:
    if not items:
        return '<p class="prerender-empty">数据生成中，请稍候…</p>'
    cards = []
    for it in items[:30]:
        zh = it.get("title_zh")
        title = _esc(zh or it.get("title") or "Untitled")
        en = _esc(it.get("title")) if zh and it.get("title") else ""
        url = _esc(it.get("url") or "#")
        source = _esc(it.get("source") or it.get("site_name") or "")
        level = _esc(it.get("signal_level") or "")
        score = _esc(it.get("signal_score") if it.get("signal_score") is not None else "")
        tldr = _esc(it.get("tldr") or "")
        sub = f'<div class="ps-en">{en}</div>' if en else ""
        tldr_html = f'<p class="ps-tldr">{tldr}</p>' if tldr else ""
        badge = f'<span class="ps-badge">[{level}] {score}</span>' if level else ""
        cards.append(
            f'<article class="ps-item" data-level="{level}">'
            f'<a class="ps-link" href="{url}" rel="noopener noreferrer" target="_blank">'
            f'{badge}<span class="ps-title">{title}</span></a>'
            f"{sub}{tldr_html}"
            f'<div class="ps-meta">{source}</div>'
            f"</article>"
        )
    return "\n".join(cards)


def build_data(payload: dict, items: list[dict]) -> str:
    slim = {
        "generated_at": payload.get("generated_at"),
        "total_items": payload.get("total_items", len(items)),
        "items": items[:30],
    }
    return (
        '<script id="__PRERENDER_DATA__" type="application/json">'
        + json.dumps(slim, ensure_ascii=False, separators=(",", ":"))
        + "</script>"
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Static prerender for AI News Radar")
    ap.add_argument("--data", default="data/latest-24h.json")
    ap.add_argument("--html", default="index.html", help="待注入的 HTML（GitHub Pages 入口）")
    ap.add_argument("--out", default="", help="输出路径，默认原地覆盖 --html")
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--og-image", default="", help="可选 Open Graph 配图绝对 URL")
    ap.add_argument("--base-url", default="https://821920046.github.io/ai-news-radar-enhanced",
                    help="站点绝对 URL（用于 canonical / og:url / sitemap）")
    ap.add_argument("--sitemap", default="sitemap.xml", help="sitemap 文件名（留空则不生成）")
    args = ap.parse_args()
    base_url = (args.base_url or "").rstrip("/")
    og_image = args.og_image or (f"{base_url}/assets/social-preview.png" if base_url else "")

    html_path = Path(args.html)
    out_path = Path(args.out) if args.out else html_path
    if not html_path.exists():
        logger.error("HTML 模板不存在: %s", html_path)
        return 1

    payload: dict = {}
    data_path = Path(args.data)
    if data_path.exists():
        try:
            payload = json.loads(data_path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("数据文件解析失败，将仅注入空骨架: %s", data_path)
    else:
        logger.warning("数据文件不存在，将仅注入空骨架: %s", data_path)

    items = (payload.get("items_ai") or payload.get("items") or [])[: max(1, args.top)]
    generated_at = payload.get("generated_at") or datetime.now(timezone.utc).isoformat()
    top_title = (items[0].get("title_zh") or items[0].get("title")) if items else None
    title_tag = f"{SITE_NAME} — {top_title}" if top_title else f"{SITE_NAME} — 24 小时 AI 资讯雷达"

    doc = html_path.read_text(encoding="utf-8")
    doc = ensure_markers(doc)
    doc = strip_seo(doc)
    doc = update_title(doc, title_tag)
    doc = fill(doc, HEAD_MARK, build_head(items, generated_at, og_image=og_image, base_url=base_url))
    doc = fill(doc, DATA_MARK, build_data(payload, items))
    doc = fill(doc, ITEMS_MARK, build_items(items))

    out_path.write_text(doc, encoding="utf-8")
    logger.info("预渲染完成: %s（注入 %d 条，generated_at=%s）", out_path, len(items), generated_at)

    if args.sitemap and base_url:
        lastmod = (generated_at or "")[:10] or datetime.now(timezone.utc).date().isoformat()
        sitemap = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f'  <url><loc>{base_url}/</loc><lastmod>{lastmod}</lastmod>'
            '<changefreq>hourly</changefreq><priority>1.0</priority></url>\n'
            '</urlset>\n'
        )
        sm_path = out_path.parent / args.sitemap
        sm_path.write_text(sitemap, encoding="utf-8")
        logger.info("sitemap 生成: %s", sm_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

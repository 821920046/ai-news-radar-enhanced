/*!
 * hot-ticker.js — 24 小时热榜 · 实时滚动组件（自包含，零依赖）
 * ---------------------------------------------------------------------------
 * 用途：替换原顶部「AI信号 / 覆盖站点 / 来源分组 / 归档总量」四张统计卡，
 *       改为「24h 最热新闻」+「24h 最热开源」两条实时横向滚动榜，
 *       鼠标悬停暂停滚动，点击任意条目弹窗查看详情。
 *
 * 用法（二选一）：
 *   1) 自定义元素：
 *        <hot-ticker data-endpoint="/hot"
 *                    data-news-json="data/latest-24h.json"
 *                    data-all-json="data/latest-24h-all.json"
 *                    data-top="20"></hot-ticker>
 *        <script type="module" src="hot-ticker.js"></script>
 *   2) 手动挂载：
 *        import { mountHotTicker } from './hot-ticker.js'
 *        mountHotTicker(document.getElementById('hot'), { endpoint: '/hot' })
 *
 * 数据来源优先级：data-endpoint(/hot) -> data-news-json + data-all-json。
 * 组件对字段做了兼容：title_zh/title、url、signal_score、signal_level、
 * tldr、source/site_name、tags、hotness/stars/score、published/published_at。
 * 所有插入文本均经过 HTML 转义，防止 XSS。
 */

;(function () {
	"use strict"

	// ── 工具 ──────────────────────────────────────────────────────────────
	function esc(s) {
		return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
			return {
				"&": "&amp;",
				"<": "&lt;",
				">": "&gt;",
				'"': "&quot;",
				"'": "&#39;",
			}[c]
		})
	}

	function toNum(v) {
		var n = parseFloat(v)
		return isFinite(n) ? n : 0
	}

	function fmtCount(n) {
		n = toNum(n)
		if (n >= 10000) return (n / 1000).toFixed(1).replace(/\.0$/, "") + "k"
		if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, "") + "k"
		return String(Math.round(n))
	}

	function relTime(iso) {
		if (!iso) return ""
		var t = new Date(String(iso).replace(" ", "T"))
		if (isNaN(t.getTime())) return ""
		var diff = (Date.now() - t.getTime()) / 1000
		if (diff < 60) return "刚刚"
		if (diff < 3600) return Math.floor(diff / 60) + " 分钟前"
		if (diff < 86400) return Math.floor(diff / 3600) + " 小时前"
		return Math.floor(diff / 86400) + " 天前"
	}

	function title(it) {
		return it.title_zh || it.title || it.name || "未命名"
	}
	function source(it) {
		return it.source || it.site_name || it.site || it.feed || ""
	}
	function url(it) {
		return it.url || it.link || it.html_url || "#"
	}
	function summary(it) {
		return it.tldr || it.summary || it.description || it.recommendation_reason || ""
	}
	function tagsOf(it) {
		var t = it.tags || it.labels || []
		return Array.isArray(t) ? t.map(String) : []
	}

	// 热度：优先显式 hotness/stars，其次 signal_score
	function hotness(it) {
		if (it.hotness != null) return toNum(it.hotness)
		if (it.stars != null) return toNum(it.stars)
		if (it.star_count != null) return toNum(it.star_count)
		return toNum(it.signal_score)
	}

	// 判断是否开源项目条目
	function isOpenSource(it) {
		if (it.is_opensource === true) return true
		var cat = String(it.category || it.type || "").toLowerCase()
		if (cat.indexOf("open") >= 0 || cat.indexOf("repo") >= 0 || cat.indexOf("github") >= 0) return true
		var src = source(it).toLowerCase()
		if (src.indexOf("github") >= 0 || src.indexOf("trending") >= 0) return true
		var tags = tagsOf(it).join(",")
		if (/开源|开源热榜|github|trending|repo/i.test(tags)) return true
		if (it.stars != null || it.star_count != null) return true
		return false
	}

	function levelOf(it) {
		return String(it.signal_level || it.level || "").toUpperCase()
	}

	// ── 数据加载 ──────────────────────────────────────────────────────────
	async function fetchJSON(u) {
		if (!u) return null
		try {
			var r = await fetch(u, { cache: "no-cache" })
			if (!r.ok) return null
			return await r.json()
		} catch (e) {
			return null
		}
	}

	function itemsFrom(payload) {
		if (!payload) return []
		if (Array.isArray(payload)) return payload
		return payload.items_ai || payload.items || payload.data || []
	}

	// 优先使用预渲染内联数据，避免首屏再请求
	function prerenderData() {
		var el = document.getElementById("__PRERENDER_DATA__")
		if (!el) return null
		try {
			return JSON.parse(el.textContent)
		} catch (e) {
			return null
		}
	}

	async function loadData(cfg) {
		// 1) /hot 接口（若部署了 API）
		if (cfg.endpoint) {
			var hot = await fetchJSON(cfg.endpoint)
			if (hot && (hot.news || hot.opensource)) {
				return {
					news: (hot.news || []).slice(0, cfg.top),
					opensource: (hot.opensource || []).slice(0, cfg.top),
					generated_at: hot.generated_at,
				}
			}
		}
		// 2) 本地 JSON / 预渲染内联数据
		var all = itemsFrom(prerenderData())
		if (!all.length) all = itemsFrom(await fetchJSON(cfg.allJson))
		if (!all.length) all = itemsFrom(await fetchJSON(cfg.newsJson))
		return deriveLanes(all, cfg.top)
	}

	// 从全量条目派生「热门新闻 / 热门开源」两个榜单
	function deriveLanes(all, top) {
		var os = [],
			news = []
		all.forEach(function (it) {
			;(isOpenSource(it) ? os : news).push(it)
		})
		var byHot = function (a, b) {
			return hotness(b) - hotness(a)
		}
		return {
			news: news.sort(byHot).slice(0, top),
			opensource: os.sort(byHot).slice(0, top),
		}
	}

	// ── 样式（注入一次）──────────────────────────────────────────────────
	var STYLE_ID = "hot-ticker-style"
	function injectStyle(root) {
		if (root.getElementById && root.getElementById(STYLE_ID)) return
		if (document.getElementById(STYLE_ID)) return
		var css = "\n" +
		".ht-wrap{--ht-bg:rgba(9,9,11,0.6);--ht-border:rgba(255,255,255,0.08);--ht-text:#e4e4e7;--ht-sub:#71717a;border:1px solid var(--ht-border);background:var(--ht-bg);border-radius:16px;padding:16px;backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);color:var(--ht-text);font:14px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;display:flex;flex-direction:column;gap:12px;box-shadow:0 10px 15px -3px rgba(0,0,0,0.3)}\n" +
		".ht-head{display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid rgba(255,255,255,0.06);padding-bottom:8px}\n" +
		".ht-title{font-weight:800;font-size:14px;display:flex;align-items:center;gap:6px;color:#fff;letter-spacing:0.5px}\n" +
		".ht-title-fire{color:#f97316;animation:ht-pulse 1.6s infinite;display:inline-block}\n" +
		"@keyframes ht-pulse{0%{transform:scale(1);opacity:0.9}50%{transform:scale(1.1);opacity:1}100%{transform:scale(1);opacity:0.9}}\n" +
		".ht-subtitle{font-size:11px;color:var(--ht-sub)}\n" +
		".ht-body{position:relative;overflow:hidden;height:56px}\n" +
		".ht-list{margin:0;padding:0;list-style:none}\n" +
		".ht-item{height:28px;display:flex;align-items:center;justify-content:space-between;font-size:13px;gap:16px}\n" +
		".ht-left-part{display:flex;align-items:center;gap:10px;min-width:0}\n" +
		".ht-rank{font-family:monospace;font-weight:700;width:16px;text-align:center;flex-shrink:0;color:var(--ht-sub)}\n" +
		".ht-rank.rank-1{color:#f43f5e;font-weight:800}\n" +
		".ht-rank.rank-2{color:#f59e0b;font-weight:800}\n" +
		".ht-rank.rank-3{color:#eab308;font-weight:800}\n" +
		".ht-link{color:#e4e4e7;text-decoration:none;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;transition:color 0.2s ease}\n" +
		".ht-link:hover{color:#2dd4bf}\n" +
		".ht-meta{font-size:11px;color:var(--ht-sub);font-family:monospace;white-space:nowrap;flex-shrink:0}\n" +
		".ht-empty{color:var(--ht-sub);font-size:12px;padding:10px 4px}\n"
		var st = document.createElement("style")
		st.id = STYLE_ID
		st.textContent = css
		;(root.head || root).appendChild ? (root.head || root).appendChild(st) : document.head.appendChild(st)
	}

	// ── 渲染 ──────────────────────────────────────────────────────────────
	function render(host, data, cfg) {
		if (!host.id) {
			host.id = "ht_" + Math.random().toString(36).substr(2, 9);
		}

		var news = (data.news || []).slice(0, 15);
		if (!news.length) {
			host.innerHTML = '<div class="ht-wrap"><div class="ht-empty">暂无热点数据</div></div>';
			return;
		}

		var itemsHtml = news.map(function (item, idx) {
			var titleText = title(item);
			var link = url(item);
			var rank = idx + 1;
			var rankClass = "ht-rank";
			if (rank === 1) rankClass += " rank-1";
			else if (rank === 2) rankClass += " rank-2";
			else if (rank === 3) rankClass += " rank-3";

			var srcCount = item.source_count || (item.merged_sources ? item.merged_sources.length : 1);
			var timeStr = relTime(item.published || item.published_at || item.date);
			var metaStr = srcCount + "个信源 · " + timeStr;

			return (
				'<li class="ht-item" style="height:28px;">' +
				'<div class="ht-left-part">' +
				'<span class="' + rankClass + '">' + rank + '</span>' +
				'<a class="ht-link" href="' + esc(link) + '" target="_blank" rel="noopener noreferrer" title="' + esc(titleText) + '">' +
				esc(titleText) +
				'</a>' +
				'</div>' +
				'<div class="ht-meta">' + esc(metaStr) + '</div>' +
				'</li>'
			);
		}).join("");

		var copyCount = Math.min(news.length, 2);
		var copyHtml = "";
		for (var i = 0; i < copyCount; i++) {
			var item = news[i];
			var titleText = title(item);
			var link = url(item);
			var rank = i + 1;
			var rankClass = "ht-rank";
			if (rank === 1) rankClass += " rank-1";
			else if (rank === 2) rankClass += " rank-2";
			else if (rank === 3) rankClass += " rank-3";

			var srcCount = item.source_count || (item.merged_sources ? item.merged_sources.length : 1);
			var timeStr = relTime(item.published || item.published_at || item.date);
			var metaStr = srcCount + "个信源 · " + timeStr;

			copyHtml += (
				'<li class="ht-item" style="height:28px;">' +
				'<div class="ht-left-part">' +
				'<span class="' + rankClass + '">' + rank + '</span>' +
				'<a class="ht-link" href="' + esc(link) + '" target="_blank" rel="noopener noreferrer" title="' + esc(titleText) + '">' +
				esc(titleText) +
				'</a>' +
				'</div>' +
				'<div class="ht-meta">' + esc(metaStr) + '</div>' +
				'</li>'
			);
		}

		host.innerHTML =
			'<div class="ht-wrap">' +
			'  <div class="ht-head">' +
			'    <div class="ht-title">' +
			'      <span class="ht-title-fire">🔥</span>' +
			'      <span>当前热点</span>' +
			'    </div>' +
			'    <div class="ht-subtitle">多信源热度 · 随时间消退</div>' +
			'  </div>' +
			'  <div class="ht-body" id="htContainer_' + host.id + '" style="position: relative; overflow: hidden; height: 56px;">' +
			'    <ul class="ht-list" id="htList_' + host.id + '" style="transform: translateY(0px);">' +
			       itemsHtml + copyHtml +
			'    </ul>' +
			'  </div>' +
			'</div>';

		// 启动垂直无缝滚动
		var container = document.getElementById("htContainer_" + host.id);
		var list = document.getElementById("htList_" + host.id);
		if (container && list && news.length > 2) {
			var rowHeight = 28;
			var itemsCount = news.length;
			var currentIndex = 0;
			var isTransitioning = false;
			var timer = null;

			var scrollFunc = function () {
				if (isTransitioning) return;
				currentIndex++;
				list.style.transition = "transform 0.5s cubic-bezier(0.4, 0, 0.2, 1)";
				list.style.transform = "translateY(-" + (currentIndex * rowHeight) + "px)";

				if (currentIndex >= itemsCount) {
					isTransitioning = true;
					setTimeout(function () {
						list.style.transition = "none";
						currentIndex = 0;
						list.style.transform = "translateY(0px)";
						isTransitioning = false;
					}, 500);
				}
			};

			timer = setInterval(scrollFunc, 3000);

			container.addEventListener("mouseenter", function () {
				if (timer) {
					clearInterval(timer);
					timer = null;
				}
			});

			container.addEventListener("mouseleave", function () {
				if (!timer) {
					timer = setInterval(scrollFunc, 3000);
				}
			});

			if (host._timer) {
				clearInterval(host._timer);
			}
			host._timer = timer;
		}
	}


	// ── 挂载 / 刷新 ───────────────────────────────────────────────────────
	async function mountHotTicker(host, options) {
		options = options || {}
		var cfg = {
			endpoint: options.endpoint || host.getAttribute("data-endpoint") || "",
			newsJson: options.newsJson || host.getAttribute("data-news-json") || "data/latest-24h.json",
			allJson: options.allJson || host.getAttribute("data-all-json") || "data/latest-24h-all.json",
			top: toNum(options.top || host.getAttribute("data-top") || 15) || 15,
			refreshMs: toNum(options.refreshMs || host.getAttribute("data-refresh") || 0),
		}
		injectStyle(document)
		host.innerHTML = '<div class="ht-wrap"><div class="ht-empty">加载热榜中…</div></div>'
		async function refresh() {
			var data = await loadData(cfg)
			render(host, data, cfg)
		}
		await refresh()
		if (cfg.refreshMs > 0) setInterval(refresh, cfg.refreshMs)
		return { refresh: refresh }
	}

	// 自定义元素
	if ("customElements" in window && !customElements.get("hot-ticker")) {
		customElements.define(
			"hot-ticker",
			class extends HTMLElement {
				connectedCallback() {
					mountHotTicker(this, {})
				}
			},
		)
	}

	// 导出
	window.mountHotTicker = mountHotTicker
	if (typeof module !== "undefined" && module.exports) {
		module.exports = { mountHotTicker: mountHotTicker }
	}
})()

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
		".ht-wrap{--ht-bg:rgba(20,24,33,.6);--ht-card:rgba(32,38,52,.7);--ht-border:rgba(255,255,255,.08);--ht-accent:#28e0a8;--ht-accent2:#5b8cff;--ht-text:#e8edf6;--ht-sub:#8b97ab;border:1px solid var(--ht-border);background:var(--ht-bg);border-radius:16px;padding:14px 16px;backdrop-filter:blur(8px);color:var(--ht-text);font:14px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'PingFang SC','Microsoft YaHei',sans-serif}\n" +
		".ht-head{display:flex;align-items:center;gap:12px;margin-bottom:10px}\n" +
		".ht-title{font-weight:700;font-size:15px;display:flex;align-items:center;gap:6px}\n" +
		".ht-live{display:inline-flex;align-items:center;gap:5px;font-size:11px;color:var(--ht-accent);font-weight:600}\n" +
		".ht-dot{width:7px;height:7px;border-radius:50%;background:var(--ht-accent);box-shadow:0 0 0 0 rgba(40,224,168,.6);animation:ht-pulse 1.6s infinite}\n" +
		"@keyframes ht-pulse{0%{box-shadow:0 0 0 0 rgba(40,224,168,.55)}70%{box-shadow:0 0 0 8px rgba(40,224,168,0)}100%{box-shadow:0 0 0 0 rgba(40,224,168,0)}}\n" +
		".ht-updated{margin-left:auto;font-size:11px;color:var(--ht-sub)}\n" +
		".ht-lane{position:relative;overflow:hidden;margin:8px 0;-webkit-mask-image:linear-gradient(90deg,transparent,#000 4%,#000 96%,transparent);mask-image:linear-gradient(90deg,transparent,#000 4%,#000 96%,transparent)}\n" +
		".ht-lane-label{font-size:11px;font-weight:700;letter-spacing:.5px;padding:2px 8px;border-radius:6px;margin-bottom:4px;display:inline-block}\n" +
		".ht-lane-news .ht-lane-label{color:#9fd0ff;background:rgba(91,140,255,.14)}\n" +
		".ht-lane-os .ht-lane-label{color:#7df0c8;background:rgba(40,224,168,.14)}\n" +
		".ht-track{display:flex;gap:10px;width:max-content;will-change:transform}\n" +
		".ht-lane:hover .ht-track{animation-play-state:paused}\n" +
		".ht-track.ht-left{animation:ht-marquee-l var(--ht-dur,40s) linear infinite}\n" +
		".ht-track.ht-right{animation:ht-marquee-r var(--ht-dur,46s) linear infinite}\n" +
		"@keyframes ht-marquee-l{from{transform:translateX(0)}to{transform:translateX(-50%)}}\n" +
		"@keyframes ht-marquee-r{from{transform:translateX(-50%)}to{transform:translateX(0)}}\n" +
		".ht-card{flex:0 0 auto;max-width:340px;display:flex;align-items:center;gap:9px;background:var(--ht-card);border:1px solid var(--ht-border);border-radius:11px;padding:8px 12px;cursor:pointer;transition:transform .15s,border-color .15s,background .15s}\n" +
		".ht-card:hover{transform:translateY(-2px);border-color:var(--ht-accent);background:rgba(40,224,168,.08)}\n" +
		".ht-rank{font-weight:800;font-size:13px;color:var(--ht-sub);min-width:20px;text-align:center}\n" +
		".ht-rank.top{color:#ffcd5b}\n" +
		".ht-card-body{min-width:0}\n" +
		".ht-card-title{font-size:13px;font-weight:600;color:var(--ht-text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:230px}\n" +
		".ht-card-meta{display:flex;align-items:center;gap:7px;font-size:11px;color:var(--ht-sub);margin-top:2px}\n" +
		".ht-badge{font-size:10px;font-weight:700;padding:1px 6px;border-radius:5px}\n" +
		".ht-badge.S{color:#ff7a7a;background:rgba(255,122,122,.14)}\n" +
		".ht-badge.A{color:#ffb05b;background:rgba(255,176,91,.14)}\n" +
		".ht-badge.B{color:#ffe45b;background:rgba(255,228,91,.12)}\n" +
		".ht-badge.C{color:#9aa7bd;background:rgba(154,167,189,.12)}\n" +
		".ht-hot{color:#ff8e53;font-weight:700}\n" +
		".ht-empty{color:var(--ht-sub);font-size:12px;padding:10px 4px}\n" +
		/* modal */
		".ht-modal{position:fixed;inset:0;z-index:9999;display:flex;align-items:center;justify-content:center;background:rgba(6,9,15,.62);backdrop-filter:blur(4px);opacity:0;pointer-events:none;transition:opacity .18s}\n" +
		".ht-modal.open{opacity:1;pointer-events:auto}\n" +
		".ht-modal-card{width:min(560px,92vw);max-height:84vh;overflow:auto;background:#161b26;border:1px solid var(--ht-border);border-radius:16px;padding:22px;box-shadow:0 24px 60px rgba(0,0,0,.5)}\n" +
		".ht-modal-card h3{margin:0 0 8px;font-size:18px;line-height:1.4;color:var(--ht-text)}\n" +
		".ht-modal-meta{display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-size:12px;color:var(--ht-sub);margin-bottom:12px}\n" +
		".ht-modal-card p{color:#cdd6e6;font-size:14px;line-height:1.7;margin:10px 0}\n" +
		".ht-tags{display:flex;flex-wrap:wrap;gap:6px;margin:12px 0}\n" +
		".ht-tag{font-size:11px;color:var(--ht-sub);background:rgba(255,255,255,.06);padding:2px 8px;border-radius:6px}\n" +
		".ht-actions{display:flex;gap:10px;margin-top:16px}\n" +
		".ht-btn{flex:1;text-align:center;padding:10px;border-radius:10px;font-weight:600;font-size:14px;text-decoration:none;cursor:pointer;border:1px solid var(--ht-border)}\n" +
		".ht-btn.primary{background:var(--ht-accent);color:#06241b;border-color:transparent}\n" +
		".ht-btn.ghost{background:transparent;color:var(--ht-sub)}\n" +
		"@media(max-width:640px){.ht-card-title{max-width:160px}}\n"
		var st = document.createElement("style")
		st.id = STYLE_ID
		st.textContent = css
		;(root.head || root).appendChild ? (root.head || root).appendChild(st) : document.head.appendChild(st)
	}

	// ── 渲染 ──────────────────────────────────────────────────────────────
	function cardHTML(it, idx) {
		var lv = levelOf(it)
		var isOS = isOpenSource(it)
		var hot = hotness(it)
		var hotStr = isOS ? "★ " + fmtCount(hot) : "🔥 " + fmtCount(hot)
		var badge = lv ? '<span class="ht-badge ' + esc(lv) + '">' + esc(lv) + "</span>" : ""
		var rankCls = idx < 3 ? "ht-rank top" : "ht-rank"
		return (
			'<div class="ht-card" data-idx="' + idx + '" role="button" tabindex="0">' +
			'<div class="' + rankCls + '">' + (idx + 1) + "</div>" +
			'<div class="ht-card-body">' +
			'<div class="ht-card-title">' + esc(title(it)) + "</div>" +
			'<div class="ht-card-meta">' + badge +
			'<span class="ht-hot">' + esc(hotStr) + "</span>" +
			"<span>" + esc(source(it)) + "</span>" +
			"</div></div></div>"
		)
	}

	function laneHTML(items, cls, label, dir) {
		if (!items.length) {
			return (
				'<div class="ht-lane ' + cls + '"><span class="ht-lane-label">' + esc(label) +
				'</span><div class="ht-empty">暂无数据</div></div>'
			)
		}
		var cards = items.map(cardHTML).join("")
		// 复制一份用于无缝循环（translateX -50%）
		var dur = Math.max(24, items.length * 3.2)
		return (
			'<div class="ht-lane ' + cls + '">' +
			'<span class="ht-lane-label">' + esc(label) + "</span>" +
			'<div class="ht-track ht-' + dir + '" style="--ht-dur:' + dur + 's">' +
			cards + cards +
			"</div></div>"
		)
	}

	function openModal(it) {
		var lv = levelOf(it)
		var isOS = isOpenSource(it)
		var hot = hotness(it)
		var tags = tagsOf(it)
		var overlay = document.createElement("div")
		overlay.className = "ht-modal"
		overlay.innerHTML =
			'<div class="ht-modal-card" role="dialog" aria-modal="true">' +
			"<h3>" + esc(title(it)) + "</h3>" +
			'<div class="ht-modal-meta">' +
			(lv ? '<span class="ht-badge ' + esc(lv) + '">' + esc(lv) + " 级</span>" : "") +
			'<span class="ht-hot">' + (isOS ? "★ " : "🔥 ") + esc(fmtCount(hot)) + "</span>" +
			"<span>" + esc(source(it)) + "</span>" +
			(relTime(it.published || it.published_at || it.date) ? "<span>" + esc(relTime(it.published || it.published_at || it.date)) + "</span>" : "") +
			"</div>" +
			(summary(it) ? "<p>" + esc(summary(it)) + "</p>" : '<p style="color:#8b97ab">暂无摘要</p>') +
			(tags.length ? '<div class="ht-tags">' + tags.slice(0, 8).map(function (t) { return '<span class="ht-tag">' + esc(t) + "</span>" }).join("") + "</div>" : "") +
			'<div class="ht-actions">' +
			'<a class="ht-btn primary" href="' + esc(url(it)) + '" target="_blank" rel="noopener noreferrer">阅读原文 ↗</a>' +
			'<button class="ht-btn ghost" data-close>关闭</button>' +
			"</div></div>"
		document.body.appendChild(overlay)
		requestAnimationFrame(function () { overlay.classList.add("open") })
		function close() {
			overlay.classList.remove("open")
			setTimeout(function () { overlay.remove() }, 200)
			document.removeEventListener("keydown", onKey)
		}
		function onKey(e) { if (e.key === "Escape") close() }
		overlay.addEventListener("click", function (e) {
			if (e.target === overlay || e.target.hasAttribute("data-close")) close()
		})
		document.addEventListener("keydown", onKey)
	}

	function render(host, data, cfg) {
		var news = data.news || []
		var os = data.opensource || []
		var updated = data.generated_at ? "更新于 " + relTime(data.generated_at) : ""
		host.innerHTML =
			'<div class="ht-wrap">' +
			'<div class="ht-head">' +
			'<span class="ht-title">🔥 24 小时热榜</span>' +
			'<span class="ht-live"><span class="ht-dot"></span>实时滚动</span>' +
			'<span class="ht-updated">' + esc(updated) + "</span>" +
			"</div>" +
			laneHTML(news, "ht-lane-news", "🗞 最热新闻", "left") +
			laneHTML(os, "ht-lane-os", "⭐ 最热开源", "right") +
			"</div>"

		// 事件委托：点击/回车打开详情
		var laneData = [
			{ sel: ".ht-lane-news", items: news },
			{ sel: ".ht-lane-os", items: os },
		]
		laneData.forEach(function (ld) {
			var lane = host.querySelector(ld.sel)
			if (!lane) return
			lane.addEventListener("click", function (e) {
				var card = e.target.closest(".ht-card")
				if (!card) return
				var it = ld.items[toNum(card.getAttribute("data-idx")) % ld.items.length]
				if (it) openModal(it)
			})
			lane.addEventListener("keydown", function (e) {
				if (e.key !== "Enter" && e.key !== " ") return
				var card = e.target.closest(".ht-card")
				if (!card) return
				e.preventDefault()
				var it = ld.items[toNum(card.getAttribute("data-idx")) % ld.items.length]
				if (it) openModal(it)
			})
		})
	}

	// ── 挂载 / 刷新 ───────────────────────────────────────────────────────
	async function mountHotTicker(host, options) {
		options = options || {}
		var cfg = {
			endpoint: options.endpoint || host.getAttribute("data-endpoint") || "",
			newsJson: options.newsJson || host.getAttribute("data-news-json") || "data/latest-24h.json",
			allJson: options.allJson || host.getAttribute("data-all-json") || "data/latest-24h-all.json",
			top: toNum(options.top || host.getAttribute("data-top") || 20) || 20,
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

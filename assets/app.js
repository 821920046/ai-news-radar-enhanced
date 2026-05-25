// ============================================================
//  AI Signal Board — app.js (Tailwind Dark Tech Edition)
//  深色科技风格 + 毛玻璃 + 流光渐变徽章 + 时间与来源精排
// ============================================================

// ---- Global State ------------------------------------------------------------
const state = {
  itemsAi: [],
  itemsAll: [],
  statsAi: [],
  totalAi: 0,
  totalAllMode: 0,
  allDataLoaded: false,
  allDataUrl: "data/latest-24h-all.json",
  allDataPromise: null,
  siteFilter: "",
  category: "",   // "" = 全部, "AI", "科技", "数码", "电脑硬件"
  query: "",
  mode: "ai",     // "ai" or "all"
  sortBy: "time", // "time" or "hot"
  waytoagiMode: "today",
  waytoagiData: null,
  sourceStatus: null,
  generatedAt: null,
};

// ---- DOM Refs ----------------------------------------------------------------
const statsGridEl        = document.getElementById("statsGrid");
const catNavEl           = document.getElementById("catNav");
const siteSelectEl       = document.getElementById("siteSelect");
const sitePillsEl        = document.getElementById("sitePills");
const newsListEl         = document.getElementById("newsList");
const updatedAtEl        = document.getElementById("updatedAt");
const searchInputEl      = document.getElementById("searchInput");
const resultCountEl      = document.getElementById("resultCount");
const listTitleEl        = document.getElementById("listTitle");
const itemTpl            = document.getElementById("itemTpl");
const modeAiBtnEl        = document.getElementById("modeAiBtn");
const modeAllBtnEl       = document.getElementById("modeAllBtn");
const modeHintEl         = document.getElementById("modeHint");
const advancedSummaryEl  = document.getElementById("advancedSummary");
const sourceHealthEl     = document.getElementById("sourceHealth");
const waytoagiUpdatedAtEl= document.getElementById("waytoagiUpdatedAt");
const waytoagiMetaEl     = document.getElementById("waytoagiMeta");
const waytoagiListEl     = document.getElementById("waytoagiList");
const waytoagiTodayBtnEl = document.getElementById("waytoagiTodayBtn");
const waytoagi7dBtnEl    = document.getElementById("waytoagi7dBtn");
const backToTopEl        = document.getElementById("backToTop");
const sortTimeBtnEl      = document.getElementById("sortTimeBtn");
const sortHotBtnEl       = document.getElementById("sortHotBtn");

// Source Health Global Indicator
const healthStatusPing   = document.querySelector(".animate-ping");
const healthStatusDot    = healthStatusPing ? healthStatusPing.nextElementSibling : null;

// ---- Source Kind Registry ----------------------------------------------------
const SOURCE_KINDS = {
  official_ai:   { label: "官方",     tone: "official" },
  aibreakfast:   { label: "日报",     tone: "newsletter" },
  followbuilders:{ label: "Builders/X", tone: "builders" },
  aihubtoday:    { label: "AI站点",   tone: "aihub" },
  aibase:        { label: "AI站点",   tone: "aihub" },
  techurls:      { label: "聚合",     tone: "aggregate" },
  buzzing:       { label: "聚合",     tone: "aggregate" },
  iris:          { label: "聚合",     tone: "aggregate" },
  bestblogs:     { label: "博客",     tone: "aggregate" },
  tophub:        { label: "聚合",     tone: "aggregate" },
  zeli:          { label: "聚合",     tone: "aggregate" },
  newsnow:       { label: "聚合",     tone: "aggregate" },
};

// ---- Category Colors (Tailwind classes) --------------------------------------
const CATEGORY_META = {
  "AI":       { text: "text-blue-400", bg: "bg-blue-500/10", border: "border-blue-500/20", activeGlow: "bg-gradient-to-r from-blue-600 to-indigo-600 shadow-blue-500/20" },
  "科技":     { text: "text-teal-400", bg: "bg-teal-500/10", border: "border-teal-500/20", activeGlow: "bg-gradient-to-r from-teal-500 to-emerald-600 shadow-teal-500/20" },
  "数码":     { text: "text-purple-400", bg: "bg-purple-500/10", border: "border-purple-500/20", activeGlow: "bg-gradient-to-r from-purple-500 to-pink-600 shadow-purple-500/20" },
  "电脑硬件": { text: "text-orange-400", bg: "bg-orange-500/10", border: "border-orange-500/20", activeGlow: "bg-gradient-to-r from-orange-500 to-red-600 shadow-orange-500/20" },
  "":         { activeGlow: "bg-gradient-to-r from-zinc-700 to-zinc-800 shadow-zinc-500/20" }
};

// ---- Utilities --------------------------------------------------------------

function fmtNumber(n) {
  return new Intl.NumberFormat("zh-CN").format(n || 0);
}

function fmtTime(iso) {
  if (!iso) return "时间未知";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  }).format(d);
}

function fmtClock(iso) {
  if (!iso) return "--:--";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "--:--";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(d);
}

function fmtDate(iso) {
  if (!iso) return "未知日期";
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit" }).format(d);
}

function debounce(fn, ms) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}

function escapeHtml(text) {
  const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
  return (text || "").replace(/[&<>"']/g, (m) => map[m]);
}

function highlightText(text, query) {
  if (!query || !text) return escapeHtml(text || "");
  const safeText = escapeHtml(text);
  const safeQuery = escapeHtml(query);
  const regex = new RegExp(`(${safeQuery.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
  return safeText.replace(regex, '<span class="bg-teal-500/30 text-teal-200 px-0.5 rounded font-bold">$1</span>');
}

function hostFromUrl(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch (_) {
    return "";
  }
}

function sourceInitial(item) {
  const text = (item.site_name || item.source || item.title || "AI").trim();
  return text.slice(0, 2).toUpperCase();
}

function fallbackReason(item) {
  if (item.tldr) return `推荐理由：这条消息已经提炼出核心结论，适合快速判断是否深入阅读。`;
  const tags = Array.isArray(item.tags) ? item.tags : [];
  if (tags.length) return `推荐理由：命中「${tags[0]}」信号，适合关注相关方向的变化。`;
  return `推荐理由：已通过 AI/科技主题过滤，适合作为今日情报流的补充线索。`;
}

// ---- Date Grouping ----------------------------------------------------------

const WEEKDAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

function fmtDateGroup(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "未知日期";
  const m = d.getMonth() + 1;
  const day = d.getDate();
  const wd = WEEKDAYS[d.getDay()];
  return `${m}月${day}日 · ${wd}`;
}

function dateKey(iso) {
  if (!iso) return "unknown";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "unknown";
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// ---- Stats Grid (visible) ---------------------------------------------------

function renderStats(payload) {
  if (!statsGridEl) return;
  statsGridEl.innerHTML = "";
  const cards = [
    { label: "AI 信号", value: fmtNumber(payload.total_items), color: "#3b82f6" },
    { label: "覆盖站点", value: fmtNumber(payload.site_count), color: "#14b8a6" },
    { label: "来源分组", value: fmtNumber(payload.source_count), color: "#8b5cf6" },
    { label: "归档总量", value: fmtNumber(payload.archive_total || 0), color: "#f97316" },
  ];
  cards.forEach(({ label, value, color }) => {
    const node = document.createElement("div");
    node.className = "glass-panel rounded-2xl p-4 transition-all duration-300 hover:border-zinc-700 hover:scale-[1.02] hover:shadow-lg hover:shadow-teal-500/5 flex flex-col justify-between relative overflow-hidden";
    node.innerHTML = `
      <div class="absolute top-0 left-0 right-0 h-[2px]" style="background: linear-gradient(90deg, ${color}, rgba(20, 184, 166, 0.4))"></div>
      <div class="text-[10px] font-bold text-zinc-500 tracking-wider uppercase">${label}</div>
      <div class="text-xl font-extrabold text-zinc-100 mt-2 font-mono tracking-tight">${value}</div>
    `;
    statsGridEl.appendChild(node);
  });
}

// ---- Category Nav -----------------------------------------------------------

function computeCategoryCounts(items) {
  const counts = { "": 0, "AI": 0, "科技": 0, "数码": 0, "电脑硬件": 0 };
  items.forEach((item) => {
    const cat = item.category || "科技";
    counts[""] += 1;
    if (counts[cat] !== undefined) counts[cat] += 1;
    else counts["科技"] += 1;
  });
  return counts;
}

function renderCategoryNav() {
  if (!catNavEl) return;
  const items = modeItems();
  const counts = computeCategoryCounts(items);

  const categories = [
    { key: "", label: "全部" },
    { key: "AI", label: "AI" },
    { key: "科技", label: "科技" },
    { key: "数码", label: "数码" },
    { key: "电脑硬件", label: "电脑硬件" },
  ];

  catNavEl.innerHTML = "";
  categories.forEach(({ key, label }) => {
    const btn = document.createElement("button");
    btn.type = "button";
    
    const count = counts[key] || 0;
    const isActive = state.category === key;
    
    if (isActive) {
      const glowClass = CATEGORY_META[key].activeGlow;
      btn.className = `flex-shrink-0 px-4 py-2 rounded-full text-xs font-bold transition-all duration-300 text-white shadow-lg border border-transparent ${glowClass}`;
    } else {
      btn.className = "flex-shrink-0 px-4 py-2 rounded-full text-xs font-semibold bg-zinc-900/40 text-zinc-400 border border-zinc-800/80 hover:border-zinc-700 hover:text-zinc-200 transition-all duration-200";
    }

    const countBadgeClass = isActive 
      ? "bg-white/20 text-white ml-2 px-1.5 py-0.5 rounded-full text-[9px]" 
      : "bg-zinc-950 text-zinc-500 border border-zinc-800 ml-2 px-1.5 py-0.5 rounded-full text-[9px]";
      
    btn.innerHTML = `${label}<span class="${countBadgeClass}">${fmtNumber(count)}</span>`;
    btn.onclick = () => {
      state.category = key;
      renderCategoryNav();
      renderList();
    };
    catNavEl.appendChild(btn);
  });
}

// ---- Advanced Summary -------------------------------------------------------

function renderAdvancedSummary() {
  if (!advancedSummaryEl) return;
  const status = state.sourceStatus;
  const allCount = state.totalAllMode || state.itemsAll.length;
  if (!status) {
    advancedSummaryEl.textContent = `全量 ${fmtNumber(allCount)} 条`;
    return;
  }
  const sites = Array.isArray(status.sites) ? status.sites : [];
  const okSites = Number(status.successful_sites || 0);
  advancedSummaryEl.textContent = `${fmtNumber(okSites)}/${fmtNumber(sites.length)} 源可用 · 全量 ${fmtNumber(allCount)} 条`;
  
  // 更新右上角呼吸灯
  updateHealthIndicator(failedCount(status) > 0 ? "warn" : "ok");
}

function failedCount(status) {
  if (!status) return 0;
  const failedSites = Array.isArray(status.failed_sites) ? status.failed_sites.length : 0;
  const failedFeeds = status.rss_opml && Array.isArray(status.rss_opml.failed_feeds) ? status.rss_opml.failed_feeds.length : 0;
  return failedSites + failedFeeds;
}

function updateHealthIndicator(tone) {
  if (!healthStatusPing || !healthStatusDot) return;
  healthStatusPing.className = "animate-ping absolute inline-flex h-full w-full rounded-full opacity-75";
  healthStatusDot.className = "relative inline-flex rounded-full h-3 w-3";
  if (tone === "ok") {
    healthStatusPing.classList.add("bg-teal-400");
    healthStatusDot.classList.add("bg-teal-500");
  } else if (tone === "warn") {
    healthStatusPing.classList.add("bg-amber-400");
    healthStatusDot.classList.add("bg-amber-500");
  } else if (tone === "bad") {
    healthStatusPing.classList.add("bg-red-500");
    healthStatusDot.classList.add("bg-red-600");
  } else {
    healthStatusPing.classList.add("bg-zinc-500");
    healthStatusDot.classList.add("bg-zinc-500");
  }
}

// ---- Site Filters -----------------------------------------------------------

function computeSiteStats(items) {
  const m = new Map();
  items.forEach((item) => {
    if (!m.has(item.site_id)) {
      m.set(item.site_id, { site_id: item.site_id, site_name: item.site_name, count: 0, raw_count: 0 });
    }
    m.get(item.site_id).count += 1;
    m.get(item.site_id).raw_count += 1;
  });
  return Array.from(m.values()).sort((a, b) => b.count - a.count || a.site_name.localeCompare(b.site_name, "zh-CN"));
}

function currentSiteStats() {
  if (state.mode === "ai") return state.statsAi || [];
  return computeSiteStats(state.itemsAll || []);
}

function renderSiteFilters() {
  const stats = currentSiteStats();

  siteSelectEl.innerHTML = '<option value="">全部站点</option>';
  stats.forEach((s) => {
    const opt = document.createElement("option");
    opt.value = s.site_id;
    opt.textContent = `${s.site_name} (${s.count})`;
    siteSelectEl.appendChild(opt);
  });
  siteSelectEl.value = state.siteFilter;

  sitePillsEl.innerHTML = "";
  const allPill = document.createElement("button");
  
  if (state.siteFilter === "") {
    allPill.className = "flex-shrink-0 px-3 py-1 rounded-lg text-xs font-bold bg-teal-500/15 text-teal-400 border border-teal-500/30 shadow-sm";
  } else {
    allPill.className = "flex-shrink-0 px-3 py-1 rounded-lg text-xs font-semibold bg-zinc-900/40 text-zinc-400 border border-zinc-800 hover:border-zinc-700 hover:text-zinc-300 transition-all";
  }
  allPill.textContent = "全部";
  allPill.onclick = () => { state.siteFilter = ""; renderSiteFilters(); renderList(); };
  sitePillsEl.appendChild(allPill);

  stats.forEach((s) => {
    const btn = document.createElement("button");
    if (state.siteFilter === s.site_id) {
      btn.className = "flex-shrink-0 px-3 py-1 rounded-lg text-xs font-bold bg-teal-500/15 text-teal-400 border border-teal-500/30 shadow-sm";
    } else {
      btn.className = "flex-shrink-0 px-3 py-1 rounded-lg text-xs font-semibold bg-zinc-900/40 text-zinc-400 border border-zinc-800 hover:border-zinc-700 hover:text-zinc-300 transition-all";
    }
    btn.textContent = `${s.site_name} (${s.count})`;
    btn.onclick = () => { state.siteFilter = s.site_id; renderSiteFilters(); renderList(); };
    sitePillsEl.appendChild(btn);
  });
}

// ---- Mode Switch ------------------------------------------------------------

function renderModeSwitch() {
  const activeClass = "bg-teal-600 text-white shadow-md shadow-teal-600/10";
  const inactiveClass = "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-900/40";
  
  modeAiBtnEl.className = `px-3.5 py-1.5 rounded-lg text-xs font-bold transition-all duration-200 ${state.mode === "ai" ? activeClass : inactiveClass}`;
  modeAllBtnEl.className = `px-3.5 py-1.5 rounded-lg text-xs font-bold transition-all duration-200 ${state.mode === "all" ? activeClass : inactiveClass}`;
  
  sortTimeBtnEl.className = `px-3.5 py-1.5 rounded-lg text-xs font-bold transition-all duration-200 ${state.sortBy === "time" ? activeClass : inactiveClass}`;
  sortHotBtnEl.className = `px-3.5 py-1.5 rounded-lg text-xs font-bold transition-all duration-200 ${state.sortBy === "hot" ? activeClass : inactiveClass}`;

  if (state.mode === "ai") {
    modeHintEl.textContent = `AI强相关信号流 · 共计 ${fmtNumber(state.totalAi)} 条数据`;
    if (listTitleEl) listTitleEl.textContent = "AI 信号流";
  } else {
    const allCount = state.totalAllMode || state.itemsAll.length;
    modeHintEl.textContent = `全量情报流 · 共计 ${fmtNumber(allCount)} 条数据`;
    if (listTitleEl) listTitleEl.textContent = "全量情报";
  }
  renderAdvancedSummary();
}

// ---- Filtering --------------------------------------------------------------

function modeItems() {
  return state.mode === "all" ? state.itemsAll : state.itemsAi;
}

function getFilteredItems() {
  const q = state.query.trim().toLowerCase();
  let items = modeItems().filter((item) => {
    if (state.siteFilter && item.site_id !== state.siteFilter) return false;
    if (state.category && (item.category || "科技") !== state.category) return false;
    if (!q) return true;
    const tags = Array.isArray(item.tags) ? item.tags.join(" ") : "";
    const hay = `${item.title || ""} ${item.title_zh || ""} ${item.title_en || ""} ${item.site_name || ""} ${item.source || ""} ${item.tldr || ""} ${item.description || ""} ${tags}`.toLowerCase();
    return hay.includes(q);
  });

  if (state.sortBy === "hot") {
    items = [...items].sort((a, b) => {
      const sa = a.hotness_score || 0;
      const sb = b.hotness_score || 0;
      if (sb !== sa) return sb - sa;
      return (b.published_at || "").localeCompare(a.published_at || "");
    });
  }

  return items;
}

// ---- Item Rendering ---------------------------------------------------------

function renderItemNode(item) {
  const node = itemTpl.content.firstElementChild.cloneNode(true);
  const category = item.category || "科技";
  const itemTime = item.published_at || item.first_seen_at;

  node.setAttribute("data-category", category);
  node.querySelector(".card-site").innerHTML = highlightText(item.site_name, state.query);
  node.querySelector(".timeline-time").textContent = fmtClock(itemTime);

  const catBadge = node.querySelector(".card-cat-badge");
  catBadge.textContent = category;
  const meta = CATEGORY_META[category] || CATEGORY_META["科技"];
  catBadge.className = `card-cat-badge px-2 py-0.5 rounded-full text-[10px] font-bold tracking-wide uppercase border ${meta.text} ${meta.bg} ${meta.border}`;

  node.querySelector(".card-source").innerHTML = highlightText(`${item.source || hostFromUrl(item.url) || "RSS"} · ${fmtTime(itemTime)}`, state.query);

  if (item.hotness_score > 0 && item.hotness_raw) {
    const badge = document.createElement("span");
    badge.className = "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold bg-orange-500/10 border border-orange-400/20 text-orange-300";
    badge.textContent = item.hotness_raw;
    node.querySelector(".card-meta").appendChild(badge);
  }

  const titleEl = node.querySelector(".card-title");
  const zh = (item.title_zh || "").trim();
  const en = (item.title_en || "").trim();
  titleEl.innerHTML = "";
  if (zh && en && zh !== en) {
    const primary = document.createElement("span");
    primary.innerHTML = highlightText(zh, state.query);
    const sub = document.createElement("span");
    sub.className = "card-title-sub text-xs text-zinc-400 font-normal mt-1 block italic font-sans leading-relaxed group-hover:text-zinc-300 transition-colors";
    sub.innerHTML = highlightText(en, state.query);
    titleEl.appendChild(primary);
    titleEl.appendChild(sub);
  } else {
    titleEl.innerHTML = highlightText(item.title || zh || en, state.query);
  }
  titleEl.href = item.url;

  const summaryEl = node.querySelector(".card-summary");
  const tldr = (item.tldr || "").trim();
  const desc = (item.description || "").trim();
  if (tldr) {
    summaryEl.innerHTML = `<span class="inline-flex items-center mr-2 px-1.5 py-0.5 rounded-md bg-teal-500/10 border border-teal-500/20 text-[10px] font-bold text-teal-300 align-middle">AI 极简</span>${highlightText(tldr, state.query)}`;
    summaryEl.classList.remove("text-zinc-400");
    summaryEl.classList.add("text-teal-100");
  } else if (desc) {
    summaryEl.innerHTML = highlightText(desc, state.query);
  } else {
    summaryEl.remove();
  }

  const tagsEl = node.querySelector(".card-tags");
  const tags = Array.isArray(item.tags) ? item.tags : [];
  if (tags.length) {
    tags.forEach((tag) => {
      const span = document.createElement("span");
      span.className = "px-2 py-0.5 rounded-md text-[10px] bg-zinc-900/60 border border-zinc-800 text-zinc-400 font-medium hover:border-zinc-700 hover:text-zinc-300 transition-colors duration-150";
      span.innerHTML = highlightText(tag, state.query);
      tagsEl.appendChild(span);
    });
  } else {
    tagsEl.remove();
  }

  const discussionEl = node.querySelector(".card-discussion");
  const host = hostFromUrl(item.url);
  const signalParts = ["关联讨论 1 条"];
  if (host) signalParts.push(host);
  if (item.hotness_raw) signalParts.push(`热榜 ${item.hotness_raw}`);
  discussionEl.textContent = signalParts.join(" · ");

  const scoreEl = node.querySelector(".card-score span:last-child");
  scoreEl.textContent = item.signal_score || Math.min(99, Math.max(60, Math.round(60 + (item.hotness_score || 0) / 25)));

  const reasonEl = node.querySelector(".card-reason");
  const reason = item.recommendation_reason || fallbackReason(item).replace(/^推荐理由：/, "");
  reasonEl.innerHTML = `<span class="text-teal-200">推荐理由：</span>${highlightText(reason, state.query)}`;

  const thumbLink = node.querySelector(".card-thumb-link");
  const thumbImg = node.querySelector(".card-thumb-img");
  const thumbFallback = node.querySelector(".card-thumb-fallback");
  const thumbInitial = node.querySelector(".card-thumb-initial");
  thumbLink.href = item.url;
  thumbImg.alt = `${item.site_name || "AI News"} image`;
  thumbInitial.textContent = sourceInitial(item);
  if (item.image_url) {
    thumbImg.src = item.image_url;
    thumbImg.onerror = () => {
      thumbImg.removeAttribute("src");
      thumbImg.classList.add("hidden");
      thumbFallback.classList.remove("hidden");
      thumbFallback.classList.add("flex");
    };
  } else {
    thumbImg.classList.add("hidden");
    thumbFallback.classList.remove("hidden");
    thumbFallback.classList.add("flex");
  }

  return node;
}

// ---- Skeleton ---------------------------------------------------------------

function renderSkeleton(count = 5) {
  const frag = document.createDocumentFragment();
  for (let i = 0; i < count; i++) {
    const card = document.createElement("div");
    card.className = "p-5 flex flex-col border-b border-zinc-900/80";
    card.innerHTML = `
      <div class="flex items-center gap-2 mb-3">
        <div class="shimmer-bg w-14 h-4 rounded-md"></div>
        <div class="shimmer-bg w-10 h-4 rounded-full"></div>
        <div class="shimmer-bg ml-auto w-16 h-3 rounded-md"></div>
      </div>
      <div class="shimmer-bg w-4/5 h-5 rounded-md mb-2"></div>
      <div class="shimmer-bg w-2/3 h-4 rounded-md mb-3"></div>
      <div class="flex gap-2">
        <div class="shimmer-bg w-12 h-4 rounded-md"></div>
        <div class="shimmer-bg w-16 h-4 rounded-md"></div>
      </div>
    `;
    frag.appendChild(card);
  }
  return frag;
}

// ---- Virtual Rendering (Lazy Load) ------------------------------------------

let currentFilteredItems = [];
let currentRenderCount = 0;
let currentGroupVal = null;
const BATCH_SIZE = 50;

const renderObserver = new IntersectionObserver((entries) => {
  if (entries[0].isIntersecting) {
    loadNextBatch();
  }
}, { rootMargin: "200px" });

function loadNextBatch() {
  const batch = currentFilteredItems.slice(currentRenderCount, currentRenderCount + BATCH_SIZE);
  if (!batch.length) return;

  const frag = document.createDocumentFragment();
  batch.forEach(item => {
    let groupVal;
    if (state.siteFilter) {
      groupVal = item.source || "未分区";
    } else {
      groupVal = dateKey(item.published_at || item.first_seen_at);
    }
    
    if (groupVal !== currentGroupVal) {
      currentGroupVal = groupVal;
      const header = document.createElement("div");
      header.className = "flex justify-between items-center px-2 sm:px-[92px] pt-1 pb-0 text-xs font-semibold text-zinc-500";
      
      const title = document.createElement("h3");
      title.className = "font-bold text-zinc-500 tracking-wide";
      if (state.siteFilter) {
         title.textContent = groupVal;
      } else {
         const sampleIso = item.published_at || item.first_seen_at;
         title.textContent = fmtDateGroup(sampleIso);
      }
      header.append(title);
      frag.appendChild(header);
    }
    
    frag.appendChild(renderItemNode(item));
  });

  currentRenderCount += batch.length;
  
  const sentinel = document.getElementById("renderSentinel");
  if (sentinel) {
    newsListEl.insertBefore(frag, sentinel);
  } else {
    newsListEl.appendChild(frag);
  }
  
  if (currentRenderCount >= currentFilteredItems.length && sentinel) {
    renderObserver.unobserve(sentinel);
    sentinel.remove();
  }
}

function renderList() {
  currentFilteredItems = getFilteredItems();
  resultCountEl.textContent = `${fmtNumber(currentFilteredItems.length)} 条`;
  newsListEl.innerHTML = "";
  currentRenderCount = 0;
  currentGroupVal = null;

  if (!currentFilteredItems.length) {
    const empty = document.createElement("div");
    empty.className = "p-10 text-center text-sm font-semibold text-zinc-500";
    empty.textContent = "当前筛选条件下没有匹配的情报结果。";
    newsListEl.appendChild(empty);
    return;
  }
  
  let sentinel = document.getElementById("renderSentinel");
  if (!sentinel) {
    sentinel = document.createElement("div");
    sentinel.id = "renderSentinel";
    sentinel.className = "h-4 w-full";
  } else {
    renderObserver.unobserve(sentinel);
  }
  
  newsListEl.appendChild(sentinel);
  renderObserver.observe(sentinel);
  
  // Kick off the first batch
  loadNextBatch();
}

// ---- WaytoAGI ---------------------------------------------------------------

function waytoagiViews(waytoagi) {
  const updates7d = Array.isArray(waytoagi?.updates_7d) ? waytoagi.updates_7d : [];
  const latestDate = waytoagi?.latest_date || (updates7d.length ? updates7d[0].date : null);
  const updatesToday = Array.isArray(waytoagi?.updates_today) && waytoagi.updates_today.length
    ? waytoagi.updates_today
    : (latestDate ? updates7d.filter((u) => u.date === latestDate) : []);
  return { updates7d, updatesToday, latestDate };
}

function renderWaytoagi(waytoagi) {
  const { updates7d, updatesToday, latestDate } = waytoagiViews(waytoagi);
  
  const activeClass = "bg-teal-600 text-white shadow-md shadow-teal-600/10";
  const inactiveClass = "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-900/40";
  waytoagiTodayBtnEl?.classList.toggle("active", state.waytoagiMode === "today");
  if (waytoagiTodayBtnEl) {
    waytoagiTodayBtnEl.className = `px-3 py-1 rounded-md text-[11px] font-bold transition-all duration-150 ${state.waytoagiMode === "today" ? activeClass : inactiveClass}`;
  }
  if (waytoagi7dBtnEl) {
    waytoagi7dBtnEl.className = `px-3 py-1 rounded-md text-[11px] font-bold transition-all duration-150 ${state.waytoagiMode === "7d" ? activeClass : inactiveClass}`;
  }
  waytoagiUpdatedAtEl.textContent = `更新时间：${fmtTime(waytoagi.generated_at)}`;

  waytoagiMetaEl.innerHTML = "";
  const rootLink = document.createElement("a");
  rootLink.href = waytoagi.root_url || "#";
  rootLink.target = "_blank";
  rootLink.rel = "noopener noreferrer";
  rootLink.className = "text-teal-400 hover:text-teal-300 font-bold underline decoration-teal-500/30 underline-offset-4";
  rootLink.textContent = "主页面";
  
  const historyLink = document.createElement("a");
  historyLink.href = waytoagi.history_url || "#";
  historyLink.target = "_blank";
  historyLink.rel = "noopener noreferrer";
  historyLink.className = "text-teal-400 hover:text-teal-300 font-bold underline decoration-teal-500/30 underline-offset-4";
  historyLink.textContent = "历史更新页";
  
  const todayCount = document.createElement("span");
  todayCount.className = "text-zinc-400 font-semibold";
  todayCount.textContent = `最近更新日(${latestDate || "--"})：${fmtNumber(waytoagi.count_today || updatesToday.length)} 条`;
  
  const weekCount = document.createElement("span");
  weekCount.className = "text-zinc-400 font-semibold";
  weekCount.textContent = `近 7 日：${fmtNumber(waytoagi.count_7d || updates7d.length)} 条`;
  
  [rootLink, "·", historyLink, "·", todayCount, "·", weekCount].forEach((part) => {
    if (typeof part === "string") {
      const sep = document.createElement("span");
      sep.className = "text-zinc-600";
      sep.textContent = ` ${part} `;
      waytoagiMetaEl.appendChild(sep);
    } else {
      waytoagiMetaEl.appendChild(part);
    }
  });

  waytoagiListEl.innerHTML = "";
  if (waytoagi.has_error) {
    const div = document.createElement("div");
    div.className = "p-5 text-center text-xs font-semibold text-red-400";
    div.textContent = waytoagi.error || "WaytoAGI 数据加载失败";
    waytoagiListEl.appendChild(div);
    return;
  }

  const updates = state.waytoagiMode === "today" ? updatesToday : updates7d;
  if (!updates.length) {
    const div = document.createElement("div");
    div.className = "p-5 text-center text-xs font-semibold text-zinc-500";
    div.textContent = state.waytoagiMode === "today"
      ? "最近更新日没有更新，可切换到近7日查看。"
      : (waytoagi.warning || "近 7 日没有更新");
    waytoagiListEl.appendChild(div);
    return;
  }

  updates.forEach((u) => {
    const row = document.createElement("a");
    row.className = "flex items-center gap-4 p-3 bg-zinc-900/20 border border-zinc-900/60 rounded-xl hover:bg-zinc-900/40 hover:border-zinc-800 transition-all duration-200 group/item";
    row.href = u.url || "#";
    row.target = "_blank";
    row.rel = "noopener noreferrer";
    
    const dateEl = document.createElement("span");
    dateEl.className = "font-mono text-[10px] text-teal-400 font-bold bg-teal-500/10 px-2 py-0.5 rounded border border-teal-500/20";
    dateEl.textContent = fmtDate(u.date);
    
    const titleEl = document.createElement("span");
    titleEl.className = "text-xs text-zinc-300 group-hover/item:text-teal-300 font-medium leading-relaxed transition-colors";
    titleEl.innerHTML = highlightText(u.title, state.query);
    
    row.append(dateEl, titleEl);
    waytoagiListEl.appendChild(row);
  });
}

// ---- Source Health -----------------------------------------------------------

function renderMetric(label, value, tone = "") {
  const node = document.createElement("div");
  node.className = "flex flex-col justify-between p-3.5 bg-zinc-900/30 border border-zinc-900 rounded-xl relative overflow-hidden";
  
  let valColor = "text-zinc-100";
  let borderColor = "border-zinc-900";
  
  if (tone === "ok") {
    valColor = "text-emerald-400";
    borderColor = "border-emerald-500/20";
    node.className += " bg-emerald-500/[0.02]";
  } else if (tone === "warn") {
    valColor = "text-amber-400";
    borderColor = "border-amber-500/20";
    node.className += " bg-amber-500/[0.02]";
  } else if (tone === "bad") {
    valColor = "text-red-400";
    borderColor = "border-red-500/20";
    node.className += " bg-red-500/[0.02]";
  }
  
  node.className += ` ${borderColor}`;

  node.innerHTML = `
    <span class="text-[10px] font-bold text-zinc-500 uppercase tracking-wide">${label}</span>
    <strong class="text-base font-extrabold font-mono mt-1 ${valColor}">${value}</strong>
  `;
  return node;
}

function renderIssueList(title, items) {
  const wrap = document.createElement("div");
  wrap.className = "p-4 border border-red-500/15 bg-red-500/5 rounded-xl";
  
  const titleEl = document.createElement("div");
  titleEl.className = "text-xs font-bold text-red-400 mb-2 uppercase tracking-wide flex items-center gap-1";
  titleEl.innerHTML = `
    <svg class="w-3.5 h-3.5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2.5" stroke="currentColor">
      <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
    </svg>
    <span>${title}</span>
  `;
  
  const list = document.createElement("ul");
  list.className = "space-y-1 pl-4 list-disc text-xs text-zinc-400 font-medium";
  items.slice(0, 6).forEach((item) => {
    const li = document.createElement("li");
    li.textContent = typeof item === "string" ? item : JSON.stringify(item);
    list.appendChild(li);
  });
  if (items.length > 6) {
    const li = document.createElement("li");
    li.className = "list-none text-zinc-500 mt-1 italic";
    li.textContent = `另外还有 ${fmtNumber(items.length - 6)} 个失败项...`;
    list.appendChild(li);
  }
  wrap.append(titleEl, list);
  return wrap;
}

function renderSourceHealth(errorMessage = "") {
  if (!sourceHealthEl) return;
  sourceHealthEl.innerHTML = "";

  const status = state.sourceStatus;
  if (!status) {
    const empty = document.createElement("div");
    empty.className = "p-4 border border-dashed border-zinc-800 rounded-xl text-xs font-semibold text-zinc-500 text-center";
    empty.textContent = errorMessage || "系统源监控状态报告尚未生成。";
    sourceHealthEl.appendChild(empty);
    renderAdvancedSummary();
    return;
  }

  const sites = Array.isArray(status.sites) ? status.sites : [];
  const failedSites = Array.isArray(status.failed_sites) ? status.failed_sites : [];
  const zeroSites = Array.isArray(status.zero_item_sites) ? status.zero_item_sites : [];
  const rss = status.rss_opml || {};
  const failedFeeds = Array.isArray(rss.failed_feeds) ? rss.failed_feeds : [];
  const skippedFeeds = Array.isArray(rss.skipped_feeds) ? rss.skipped_feeds : [];
  const replacedFeeds = Array.isArray(rss.replaced_feeds) ? rss.replaced_feeds : [];

  const metricGrid = document.createElement("div");
  metricGrid.className = "grid grid-cols-2 md:grid-cols-4 gap-3";
  metricGrid.append(
    renderMetric("官方/内置源", `${fmtNumber(status.successful_sites || 0)}/${fmtNumber(sites.length)}`,
      failedSites.length ? "warn" : "ok"),
    renderMetric("订阅(RSS)源", rss.enabled
      ? `${fmtNumber(rss.ok_feeds || 0)}/${fmtNumber(rss.effective_feed_total || 0)}`
      : "未启用"),
    renderMetric("失败采集源", fmtNumber(failedSites.length + failedFeeds.length),
      failedSites.length || failedFeeds.length ? "bad" : "ok"),
    renderMetric("替换/忽略RSS", `${fmtNumber(replacedFeeds.length)}/${fmtNumber(skippedFeeds.length)}`)
  );
  sourceHealthEl.appendChild(metricGrid);

  const issues = document.createElement("div");
  issues.className = "grid grid-cols-1 md:grid-cols-2 gap-3 mt-4";
  
  if (failedSites.length) issues.appendChild(renderIssueList("内置源抓取异常", failedSites));
  if (zeroSites.length)   issues.appendChild(renderIssueList("空数据反馈源 (24h)", zeroSites));
  if (failedFeeds.length) issues.appendChild(renderIssueList("RSS 种子解析异常", failedFeeds));
  if (skippedFeeds.length) {
    issues.appendChild(renderIssueList("过滤/跳过RSS", skippedFeeds.map((item) => `${item.feed_url} (${item.reason || "skipped"})`)));
  }
  
  if (issues.childElementCount) {
    sourceHealthEl.appendChild(issues);
  } else {
    const ok = document.createElement("div");
    ok.className = "p-3.5 border border-emerald-500/15 bg-emerald-500/5 rounded-xl text-xs font-bold text-emerald-400 flex items-center gap-1.5";
    ok.innerHTML = `
      <svg class="w-4 h-4 text-emerald-400" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2.5" stroke="currentColor">
        <path stroke-linecap="round" stroke-linejoin="round" d="M9 12.75L11.25 15 15 9.75M21 12c0 1.268-.63 2.39-1.593 3.068a3.745 3.745 0 01-1.043 3.296 3.745 3.745 0 01-3.296 1.043A3.745 3.745 0 0112 21c-1.268 0-2.39-.63-3.068-1.593a3.746 3.746 0 01-3.296-1.043 3.745 3.745 0 01-1.043-3.296A3.745 3.745 0 013 12c0-1.268.63-2.39 1.593-3.068a3.745 3.745 0 011.043-3.296 3.746 3.746 0 013.296-1.043A3.746 3.746 0 0112 3c1.268 0 2.39.63 3.068 1.593a3.746 3.746 0 013.296 1.043 3.746 3.746 0 011.043 3.296A3.745 3.745 0 0121 12z" />
      </svg>
      <span>所有采集节点运作正常，数据一致性验证通过。</span>
    `;
    sourceHealthEl.appendChild(ok);
  }
  renderAdvancedSummary();
}

// ---- Data Loaders -----------------------------------------------------------

async function loadNewsData() {
  const res = await fetch(`./data/latest-24h.json?t=${Date.now()}`);
  if (!res.ok) throw new Error(`加载 latest-24h.json 失败: ${res.status}`);
  return res.json();
}

async function loadAllModeData() {
  if (state.allDataLoaded) return;
  if (!state.allDataPromise) {
    state.allDataPromise = fetch(`./${state.allDataUrl}?t=${Date.now()}`)
      .then((res) => {
        if (!res.ok) throw new Error(`加载 latest-24h-all.json 失败: ${res.status}`);
        return res.json();
      })
      .then((payload) => {
        state.itemsAll = payload.items_all || state.itemsAi;
        state.totalAllMode = payload.total_items_all_mode || state.itemsAll.length;
        state.allDataLoaded = true;
      })
      .catch((err) => {
        state.allDataPromise = null;
        throw err;
      });
  }
  return state.allDataPromise;
}

async function loadWaytoagiData() {
  const res = await fetch(`./data/waytoagi-7d.json?t=${Date.now()}`);
  if (!res.ok) throw new Error(`加载 waytoagi-7d.json 失败: ${res.status}`);
  return res.json();
}

async function loadSourceStatusData() {
  const res = await fetch(`./data/source-status.json?t=${Date.now()}`);
  if (!res.ok) throw new Error(`加载 source-status.json 失败: ${res.status}`);
  return res.json();
}

// ---- Init -------------------------------------------------------------------

async function init() {
  newsListEl.innerHTML = "";
  newsListEl.appendChild(renderSkeleton(6));

  const [newsResult, waytoagiResult, statusResult] = await Promise.allSettled([
    loadNewsData(),
    loadWaytoagiData(),
    loadSourceStatusData(),
  ]);

  if (newsResult.status === "fulfilled") {
    const payload = newsResult.value;
    state.itemsAi       = payload.items_ai || payload.items || [];
    state.itemsAll      = payload.items_all || [];
    state.statsAi       = payload.site_stats || [];
    state.totalAi       = payload.total_items || state.itemsAi.length;
    state.totalAllMode  = payload.total_items_all_mode || state.itemsAll.length;
    state.allDataUrl    = payload.all_mode_data_url || state.allDataUrl;
    state.allDataLoaded = Boolean(payload.items_all);
    state.generatedAt   = payload.generated_at;

    renderStats(payload);
    renderModeSwitch();
    renderCategoryNav();
    renderSiteFilters();
    renderList();
    updatedAtEl.textContent = fmtTime(state.generatedAt);
  } else {
    updatedAtEl.textContent = "加载失败";
    newsListEl.innerHTML = `<div class="p-10 text-center text-sm text-red-400 font-semibold">${newsResult.reason.message}</div>`;
  }

  if (statusResult.status === "fulfilled") {
    state.sourceStatus = statusResult.value;
    renderSourceHealth();
  } else {
    renderSourceHealth(statusResult.reason?.message);
  }

  if (waytoagiResult.status === "fulfilled") {
    state.waytoagiData = waytoagiResult.value;
    renderWaytoagi(state.waytoagiData);
  } else {
    waytoagiUpdatedAtEl.textContent = "加载失败";
    waytoagiListEl.innerHTML = `<div class="p-5 text-center text-xs font-semibold text-red-400">${waytoagiResult.reason?.message}</div>`;
  }
}

// ---- Event Listeners --------------------------------------------------------

const debouncedSearch = debounce((value) => {
  state.query = value;
  renderList();
}, 150);

searchInputEl.addEventListener("input", (e) => debouncedSearch(e.target.value));

searchInputEl.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    searchInputEl.value = "";
    state.query = "";
    renderList();
    searchInputEl.blur();
  }
});

siteSelectEl.addEventListener("change", (e) => {
  state.siteFilter = e.target.value;
  renderSiteFilters();
  renderList();
});

modeAiBtnEl.addEventListener("click", () => {
  state.mode = "ai";
  renderModeSwitch();
  renderCategoryNav();
  renderSiteFilters();
  renderList();
});

modeAllBtnEl.addEventListener("click", async () => {
  state.mode = "all";
  renderModeSwitch();
  newsListEl.innerHTML = "";
  newsListEl.appendChild(renderSkeleton(6));
  try {
    await loadAllModeData();
    renderCategoryNav();
    renderSiteFilters();
    renderList();
  } catch (err) {
    newsListEl.innerHTML = `<div class="p-10 text-center text-sm font-semibold text-red-400">${err.message}</div>`;
  }
});

sortTimeBtnEl?.addEventListener("click", () => {
  state.sortBy = "time";
  renderModeSwitch();
  renderList();
});

sortHotBtnEl?.addEventListener("click", () => {
  state.sortBy = "hot";
  renderModeSwitch();
  renderList();
});

waytoagiTodayBtnEl?.addEventListener("click", () => {
  state.waytoagiMode = "today";
  if (state.waytoagiData) renderWaytoagi(state.waytoagiData);
});

waytoagi7dBtnEl?.addEventListener("click", () => {
  state.waytoagiMode = "7d";
  if (state.waytoagiData) renderWaytoagi(state.waytoagiData);
});

// ---- Back to Top ------------------------------------------------------------

window.addEventListener("scroll", () => {
  const isHidden = window.scrollY <= window.innerHeight * 1.5;
  backToTopEl.classList.toggle("opacity-0", isHidden);
  backToTopEl.classList.toggle("pointer-events-none", isHidden);
  backToTopEl.classList.toggle("translate-y-4", isHidden);
}, { passive: true });

backToTopEl.addEventListener("click", () => {
  window.scrollTo({ top: 0, behavior: "smooth" });
});

// ---- Launch -----------------------------------------------------------------

init();

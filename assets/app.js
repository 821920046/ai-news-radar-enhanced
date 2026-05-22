// ============================================================
//  AI Signal Board — app.js (Tailwind Dark Tech Edition)
//  深色科技风格 + 毛玻璃 + 流光渐变徽章 + 时间与来源精排
// ============================================================

// ---- Global State ------------------------------------------------------------
const state = {
  itemsAi: [],
  itemsAll: [],
  itemsAllRaw: [],
  statsAi: [],
  totalAi: 0,
  totalRaw: 0,
  totalAllMode: 0,
  allDedup: true,
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
const allDedupeWrapEl    = document.getElementById("allDedupeWrap");
const allDedupeToggleEl  = document.getElementById("allDedupeToggle");
const allDedupeLabelEl   = document.getElementById("allDedupeLabel");
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

function sourceKind(siteId) {
  return SOURCE_KINDS[siteId] || { label: "来源", tone: "default" };
}

function siteRows() {
  return Array.isArray(state.sourceStatus?.sites) ? state.sourceStatus.sites : [];
}

function siteRow(siteId) {
  return siteRows().find((s) => s.site_id === siteId) || null;
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
    else counts["科技"] += 1; // fallback
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
  const allCount = state.allDedup
    ? (state.totalAllMode || state.itemsAll.length)
    : (state.totalRaw || state.itemsAllRaw.length);
  if (!status) {
    advancedSummaryEl.textContent = `全量 ${fmtNumber(allCount)} 条`;
    return;
  }
  const sites = Array.isArray(status.sites) ? status.sites : [];
  const okSites = Number(status.successful_sites || 0);
  advancedSummaryEl.textContent = `${fmtNumber(okSites)}/${fmtNumber(sites.length)} 源可用 · 全量 ${fmtNumber(allCount)} 条`;
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
  return computeSiteStats(state.allDedup ? (state.itemsAll || []) : (state.itemsAllRaw || []));
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

  // 去重栏在 Tailwind 里需要显示为 flex
  allDedupeWrapEl?.classList.toggle("hidden", state.mode !== "all");
  allDedupeWrapEl?.classList.toggle("flex", state.mode === "all");

  if (allDedupeToggleEl) allDedupeToggleEl.checked = state.allDedup;
  if (allDedupeLabelEl)  allDedupeLabelEl.textContent = state.allDedup ? "已去重" : "未去重";

  if (state.mode === "ai") {
    modeHintEl.textContent = `AI强相关信号流 · 共计 ${fmtNumber(state.totalAi)} 条数据`;
    if (listTitleEl) listTitleEl.textContent = "AI 信号流";
  } else {
    const allCount = state.allDedup
      ? (state.totalAllMode || state.itemsAll.length)
      : (state.totalRaw || state.itemsAllRaw.length);
    modeHintEl.textContent = `全量情报流 (${state.allDedup ? "已进行高置信度去重" : "显示所有原始信号"}) · 共计 ${fmtNumber(allCount)} 条数据`;
    if (listTitleEl) listTitleEl.textContent = "全量情报";
  }
  renderAdvancedSummary();
}

// ---- Filtering --------------------------------------------------------------

function effectiveAllItems() {
  return state.allDedup ? state.itemsAll : state.itemsAllRaw;
}

function modeItems() {
  return state.mode === "all" ? effectiveAllItems() : state.itemsAi;
}

function getFilteredItems() {
  const q = state.query.trim().toLowerCase();
  let items = modeItems().filter((item) => {
    // Site filter
    if (state.siteFilter && item.site_id !== state.siteFilter) return false;
    // Category filter
    if (state.category && (item.category || "科技") !== state.category) return false;
    // Search filter
    if (!q) return true;
    const tags = Array.isArray(item.tags) ? item.tags.join(" ") : "";
    const hay = `${item.title || ""} ${item.title_zh || ""} ${item.title_en || ""} ${item.site_name || ""} ${item.source || ""} ${item.description || ""} ${tags}`.toLowerCase();
    return hay.includes(q);
  });

  // Sort by hotness
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

  // 分类左边框 (via data attribute + CSS)
  node.setAttribute("data-category", category);

  // 站点名字
  node.querySelector(".card-site").textContent = item.site_name;

  // 分类 Badge 配色渲染
  const catBadge = node.querySelector(".card-cat-badge");
  catBadge.textContent = category;
  const meta = CATEGORY_META[category] || CATEGORY_META["科技"];
  catBadge.className = `card-cat-badge px-2 py-0.5 rounded-full text-[10px] font-bold tracking-wide uppercase border ${meta.text} ${meta.bg} ${meta.border}`;

  // 来源分区
  node.querySelector(".card-source").textContent = `分区: ${item.source}`;

  // 时间格式化
  node.querySelector(".card-time").textContent = fmtTime(item.published_at || item.first_seen_at);

  // 热度指标
  if (item.hotness_score > 0 && item.hotness_raw) {
    const badge = document.createElement("span");
    badge.className = "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-extrabold bg-gradient-to-r from-orange-500 to-red-600 text-white shadow-sm shadow-red-500/20";
    badge.innerHTML = `
      <svg class="w-2.5 h-2.5 text-white animate-pulse" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor">
        <path fill-rule="evenodd" d="M12.963 2.285a.75.75 0 00-1.071-.136 9.707 9.707 0 00-.73 9.694l.519.954a.75.75 0 101.32-.718l-.518-.954a8.207 8.207 0 01.618-8.006.75.75 0 00-.138-.833zM7.403 4.274a.75.75 0 00-1.015-.251 9.72 9.72 0 00-4.485 8.148 9.75 9.75 0 0019.227 2.034 9.73 9.73 0 00-3.135-7.463.75.75 0 00-1.017.072l-1.06 1.14a7.22 7.22 0 01-1.636 1.34l-.53.31a.75.75 0 10.764 1.288l.53-.31a8.721 8.721 0 002.492-2.148 8.25 8.25 0 01-14.717 3.562c.18-.838.487-1.65.91-2.4l1.012-1.802a.75.75 0 00-.39-.997l-1.06-.415z" clip-rule="evenodd" />
      </svg>
      <span>${item.hotness_raw}</span>
    `;
    node.querySelector(".card-meta").appendChild(badge);
  }

  // 双语标题
  const titleEl = node.querySelector(".card-title");
  const zh = (item.title_zh || "").trim();
  const en = (item.title_en || "").trim();
  titleEl.textContent = "";
  if (zh && en && zh !== en) {
    const primary = document.createElement("span");
    primary.textContent = zh;
    const sub = document.createElement("span");
    sub.className = "card-title-sub text-xs text-zinc-400 font-normal mt-1 block italic font-sans leading-relaxed group-hover:text-zinc-300 transition-colors";
    sub.textContent = en;
    titleEl.appendChild(primary);
    titleEl.appendChild(sub);
  } else {
    titleEl.textContent = item.title || zh || en;
  }
  titleEl.href = item.url;

  // 摘要
  const summaryEl = node.querySelector(".card-summary");
  const desc = (item.description || "").trim();
  if (desc) {
    summaryEl.textContent = desc;
  } else {
    summaryEl.remove();
  }

  // 标签
  const tagsEl = node.querySelector(".card-tags");
  const tags = Array.isArray(item.tags) ? item.tags : [];
  if (tags.length) {
    tags.forEach((tag) => {
      const span = document.createElement("span");
      span.className = "px-2 py-0.5 rounded-md text-[10px] bg-zinc-900/60 border border-zinc-800 text-zinc-400 font-medium hover:border-zinc-700 hover:text-zinc-300 transition-colors duration-150";
      span.textContent = tag;
      tagsEl.appendChild(span);
    });
  } else {
    tagsEl.remove();
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

// ---- Date-grouped rendering -------------------------------------------------

function renderDateGrouped(items) {
  // Group by date key, preserving insertion order (items are pre-sorted by time desc)
  const groups = new Map();
  items.forEach((item) => {
    const key = dateKey(item.published_at || item.first_seen_at);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  });

  const frag = document.createDocumentFragment();
  for (const [key, groupItems] of groups) {
    const header = document.createElement("div");
    header.className = "flex justify-between items-center px-6 py-2.5 bg-zinc-900/10 text-xs font-semibold text-zinc-400 border-b border-zinc-900";
    
    const title = document.createElement("h3");
    const sampleIso = groupItems[0].published_at || groupItems[0].first_seen_at;
    title.className = "font-bold text-zinc-400";
    title.textContent = fmtDateGroup(sampleIso);
    
    const count = document.createElement("span");
    count.className = "font-mono font-bold text-[10px] text-zinc-500 bg-zinc-950 px-1.5 py-0.5 border border-zinc-900 rounded";
    count.textContent = `${fmtNumber(groupItems.length)} 条`;
    
    header.append(title, count);
    frag.appendChild(header);

    groupItems.forEach((item) => frag.appendChild(renderItemNode(item)));
  }

  newsListEl.appendChild(frag);
}

// ---- Source-grouped rendering -----------------------------------------------

function groupBySource(items) {
  const groupMap = new Map();
  items.forEach((item) => {
    const key = item.source || "未分区";
    if (!groupMap.has(key)) groupMap.set(key, []);
    groupMap.get(key).push(item);
  });
  return Array.from(groupMap.entries())
    .sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0], "zh-CN"));
}

function renderGroupedBySource(items) {
  const groups = groupBySource(items);
  const frag = document.createDocumentFragment();
  groups.forEach(([source, groupItems]) => {
    const section = document.createElement("section");
    const header = document.createElement("header");
    header.className = "flex justify-between items-center px-6 py-2.5 bg-zinc-900/10 text-xs font-semibold text-zinc-400 border-b border-zinc-900";
    
    const title = document.createElement("h3");
    title.className = "font-bold text-zinc-400";
    title.textContent = source;
    
    const count = document.createElement("span");
    count.className = "font-mono font-bold text-[10px] text-zinc-500 bg-zinc-950 px-1.5 py-0.5 border border-zinc-900 rounded";
    count.textContent = `${fmtNumber(groupItems.length)} 条`;
    
    header.append(title, count);
    section.appendChild(header);
    groupItems.forEach((item) => section.appendChild(renderItemNode(item)));
    frag.appendChild(section);
  });
  newsListEl.appendChild(frag);
}

// ---- Main render dispatcher -------------------------------------------------

function renderList() {
  const filtered = getFilteredItems();
  resultCountEl.textContent = `${fmtNumber(filtered.length)} 条`;
  newsListEl.innerHTML = "";

  if (!filtered.length) {
    const empty = document.createElement("div");
    empty.className = "p-10 text-center text-sm font-semibold text-zinc-500";
    empty.textContent = "当前筛选条件下没有匹配的情报结果。";
    newsListEl.appendChild(empty);
    return;
  }

  if (state.siteFilter) {
    renderGroupedBySource(filtered);
  } else {
    renderDateGrouped(filtered);
  }
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
    titleEl.textContent = u.title;
    
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
        state.itemsAllRaw = payload.items_all_raw || payload.items_all || state.itemsAi;
        state.itemsAll = payload.items_all || state.itemsAi;
        state.totalRaw = payload.total_items_raw || state.itemsAllRaw.length;
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
    state.itemsAllRaw   = payload.items_all_raw || payload.items_all || [];
    state.itemsAll      = payload.items_all || [];
    state.statsAi       = payload.site_stats || [];
    state.totalAi       = payload.total_items || state.itemsAi.length;
    state.totalRaw      = payload.total_items_raw || state.itemsAllRaw.length;
    state.totalAllMode  = payload.total_items_all_mode || state.itemsAll.length;
    state.allDataUrl    = payload.all_mode_data_url || state.allDataUrl;
    state.allDataLoaded = Boolean(payload.items_all || payload.items_all_raw);
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

if (allDedupeToggleEl) {
  allDedupeToggleEl.addEventListener("change", (e) => {
    state.allDedup = Boolean(e.target.checked);
    renderModeSwitch();
    renderCategoryNav();
    renderSiteFilters();
    renderList();
  });
}

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

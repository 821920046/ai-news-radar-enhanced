// ============================================================
//  AI Signal Board — app.js
//  暖色调编辑风格 + 分类导航 + 时间分组 + 来源筛选
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

// ---- Category Colors (for JS-generated badges) ------------------------------
const CATEGORY_META = {
  "AI":       { color: "#2563eb", bg: "#eff6ff" },
  "科技":     { color: "#0d9488", bg: "#f0fdfa" },
  "数码":     { color: "#7c3aed", bg: "#f5f3ff" },
  "电脑硬件": { color: "#ea580c", bg: "#fff7ed" },
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
    { label: "AI 信号", value: fmtNumber(payload.total_items), color: "#2563eb" },
    { label: "覆盖站点", value: fmtNumber(payload.site_count), color: "#0d9488" },
    { label: "来源分组", value: fmtNumber(payload.source_count), color: "#7c3aed" },
    { label: "归档总量", value: fmtNumber(payload.archive_total || 0), color: "#ea580c" },
  ];
  cards.forEach(({ label, value, color }) => {
    const node = document.createElement("div");
    node.className = "stat-card";
    node.style.borderTopColor = color;
    node.innerHTML = `<div class="stat-label">${label}</div><div class="stat-value">${value}</div>`;
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
    btn.className = `cat-btn ${state.category === key ? "active" : ""}`;
    if (key) btn.setAttribute("data-cat", key);
    btn.type = "button";

    const count = counts[key] || 0;
    btn.innerHTML = `${label}<span class="cat-count">${fmtNumber(count)}</span>`;
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
  allPill.className = `site-pill ${state.siteFilter === "" ? "active" : ""}`;
  allPill.textContent = "全部";
  allPill.onclick = () => { state.siteFilter = ""; renderSiteFilters(); renderList(); };
  sitePillsEl.appendChild(allPill);

  stats.forEach((s) => {
    const btn = document.createElement("button");
    btn.className = `site-pill ${state.siteFilter === s.site_id ? "active" : ""}`;
    btn.textContent = `${s.site_name} ${s.count}`;
    btn.onclick = () => { state.siteFilter = s.site_id; renderSiteFilters(); renderList(); };
    sitePillsEl.appendChild(btn);
  });
}

// ---- Mode Switch ------------------------------------------------------------

function renderModeSwitch() {
  modeAiBtnEl.classList.toggle("active", state.mode === "ai");
  modeAllBtnEl.classList.toggle("active", state.mode === "all");
  sortTimeBtnEl?.classList.toggle("active", state.sortBy === "time");
  sortHotBtnEl?.classList.toggle("active", state.sortBy === "hot");
  allDedupeWrapEl?.classList.toggle("show", state.mode === "all");
  if (allDedupeToggleEl) allDedupeToggleEl.checked = state.allDedup;
  if (allDedupeLabelEl)  allDedupeLabelEl.textContent = state.allDedup ? "去重开" : "去重关";

  if (state.mode === "ai") {
    modeHintEl.textContent = `AI强相关 · ${fmtNumber(state.totalAi)} 条`;
    if (listTitleEl) listTitleEl.textContent = "AI 信号流";
  } else {
    const allCount = state.allDedup
      ? (state.totalAllMode || state.itemsAll.length)
      : (state.totalRaw || state.itemsAllRaw.length);
    modeHintEl.textContent = `全量 · ${state.allDedup ? "去重开" : "去重关"} · ${fmtNumber(allCount)} 条`;
    if (listTitleEl) listTitleEl.textContent = "全量更新";
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
  const kind = sourceKind(item.site_id);
  const category = item.category || "科技";

  // Category-based left border (via data attribute + CSS)
  node.setAttribute("data-category", category);

  // Site name
  node.querySelector(".card-site").textContent = item.site_name;

  // Category badge
  const catBadge = node.querySelector(".card-cat-badge");
  catBadge.textContent = category;
  catBadge.setAttribute("data-cat", category);

  // Source section
  node.querySelector(".card-source").textContent = `分区: ${item.source}`;

  // Time
  node.querySelector(".card-time").textContent = fmtTime(item.published_at || item.first_seen_at);

  // Hotness badge
  if (item.hotness_score > 0 && item.hotness_raw) {
    const badge = document.createElement("span");
    badge.className = "hotness-badge";
    badge.textContent = item.hotness_raw;
    node.querySelector(".card-meta").appendChild(badge);
  }

  // Title (bilingual)
  const titleEl = node.querySelector(".card-title");
  const zh = (item.title_zh || "").trim();
  const en = (item.title_en || "").trim();
  titleEl.textContent = "";
  if (zh && en && zh !== en) {
    const primary = document.createElement("span");
    primary.textContent = zh;
    const sub = document.createElement("span");
    sub.className = "card-title-sub";
    sub.textContent = en;
    titleEl.appendChild(primary);
    titleEl.appendChild(sub);
  } else {
    titleEl.textContent = item.title || zh || en;
  }
  titleEl.href = item.url;

  // Summary (description)
  const summaryEl = node.querySelector(".card-summary");
  const desc = (item.description || "").trim();
  if (desc) {
    summaryEl.textContent = desc;
  } else {
    summaryEl.remove();
  }

  // Tags
  const tagsEl = node.querySelector(".card-tags");
  const tags = Array.isArray(item.tags) ? item.tags : [];
  if (tags.length) {
    tags.forEach((tag) => {
      const span = document.createElement("span");
      span.className = "card-tag";
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
    card.className = "skeleton-card";
    card.innerHTML = `
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">
        <div class="skeleton" style="width:60px;height:14px"></div>
        <div class="skeleton" style="width:36px;height:14px;border-radius:9999px"></div>
        <div class="skeleton" style="margin-left:auto;width:72px;height:14px"></div>
      </div>
      <div class="skeleton" style="width:88%;height:16px"></div>
      <div class="skeleton" style="margin-top:6px;width:70%;height:12px"></div>
      <div style="display:flex;gap:5px;margin-top:8px;">
        <div class="skeleton" style="width:48px;height:16px;border-radius:9999px"></div>
        <div class="skeleton" style="width:56px;height:16px;border-radius:9999px"></div>
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
    header.className = "time-group-head";
    const title = document.createElement("h3");
    const sampleIso = groupItems[0].published_at || groupItems[0].first_seen_at;
    title.textContent = fmtDateGroup(sampleIso);
    const count = document.createElement("span");
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
    header.className = "source-group-head";
    const title = document.createElement("h3");
    title.textContent = source;
    const count = document.createElement("span");
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
    empty.className = "empty-state";
    empty.textContent = "当前筛选条件下没有结果。";
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
  waytoagiTodayBtnEl?.classList.toggle("active", state.waytoagiMode === "today");
  waytoagi7dBtnEl?.classList.toggle("active", state.waytoagiMode === "7d");
  waytoagiUpdatedAtEl.textContent = `更新时间：${fmtTime(waytoagi.generated_at)}`;

  waytoagiMetaEl.innerHTML = "";
  const rootLink = document.createElement("a");
  rootLink.href = waytoagi.root_url || "#";
  rootLink.target = "_blank";
  rootLink.rel = "noopener noreferrer";
  rootLink.textContent = "主页面";
  const historyLink = document.createElement("a");
  historyLink.href = waytoagi.history_url || "#";
  historyLink.target = "_blank";
  historyLink.rel = "noopener noreferrer";
  historyLink.textContent = "历史更新页";
  const todayCount = document.createElement("span");
  todayCount.textContent = `最近更新日(${latestDate || "--"})：${fmtNumber(waytoagi.count_today || updatesToday.length)} 条`;
  const weekCount = document.createElement("span");
  weekCount.textContent = `近 7 日：${fmtNumber(waytoagi.count_7d || updates7d.length)} 条`;
  [rootLink, "·", historyLink, "·", todayCount, "·", weekCount].forEach((part) => {
    if (typeof part === "string") {
      const sep = document.createElement("span");
      sep.textContent = part;
      waytoagiMetaEl.appendChild(sep);
    } else {
      waytoagiMetaEl.appendChild(part);
    }
  });

  waytoagiListEl.innerHTML = "";
  if (waytoagi.has_error) {
    const div = document.createElement("div");
    div.className = "waytoagi-error";
    div.textContent = waytoagi.error || "WaytoAGI 数据加载失败";
    waytoagiListEl.appendChild(div);
    return;
  }

  const updates = state.waytoagiMode === "today" ? updatesToday : updates7d;
  if (!updates.length) {
    const div = document.createElement("div");
    div.className = "waytoagi-empty";
    div.textContent = state.waytoagiMode === "today"
      ? "最近更新日没有更新，可切换到近7日查看。"
      : (waytoagi.warning || "近 7 日没有更新");
    waytoagiListEl.appendChild(div);
    return;
  }

  updates.forEach((u) => {
    const row = document.createElement("a");
    row.className = "waytoagi-item";
    row.href = u.url || "#";
    row.target = "_blank";
    row.rel = "noopener noreferrer";
    const dateEl = document.createElement("span");
    dateEl.className = "d";
    dateEl.textContent = fmtDate(u.date);
    const titleEl = document.createElement("span");
    titleEl.className = "t";
    titleEl.textContent = u.title;
    row.append(dateEl, titleEl);
    waytoagiListEl.appendChild(row);
  });
}

// ---- Source Health -----------------------------------------------------------

function renderMetric(label, value, tone = "") {
  const node = document.createElement("div");
  node.className = `health-card ${tone}`.trim();
  const labelEl = document.createElement("span");
  labelEl.className = "health-label";
  labelEl.textContent = label;
  const valueEl = document.createElement("strong");
  valueEl.textContent = value;
  node.append(labelEl, valueEl);
  return node;
}

function renderIssueList(title, items) {
  const wrap = document.createElement("div");
  wrap.className = "health-issue";
  const titleEl = document.createElement("div");
  titleEl.className = "health-issue-title";
  titleEl.textContent = title;
  const list = document.createElement("ul");
  items.slice(0, 6).forEach((item) => {
    const li = document.createElement("li");
    li.textContent = typeof item === "string" ? item : JSON.stringify(item);
    list.appendChild(li);
  });
  if (items.length > 6) {
    const li = document.createElement("li");
    li.textContent = `另有 ${fmtNumber(items.length - 6)} 项`;
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
    empty.className = "health-empty";
    empty.textContent = errorMessage || "源状态未生成";
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
  metricGrid.className = "health-grid";
  metricGrid.append(
    renderMetric("内置源", `${fmtNumber(status.successful_sites || 0)}/${fmtNumber(sites.length)}`,
      failedSites.length ? "warn" : "ok"),
    renderMetric("RSS", rss.enabled
      ? `${fmtNumber(rss.ok_feeds || 0)}/${fmtNumber(rss.effective_feed_total || 0)}`
      : "未启用"),
    renderMetric("失败源", fmtNumber(failedSites.length + failedFeeds.length),
      failedSites.length || failedFeeds.length ? "bad" : "ok"),
    renderMetric("替换/跳过", `${fmtNumber(replacedFeeds.length)}/${fmtNumber(skippedFeeds.length)}`)
  );
  sourceHealthEl.appendChild(metricGrid);

  const issues = document.createElement("div");
  issues.className = "health-issues";
  if (failedSites.length) issues.appendChild(renderIssueList("失败站点", failedSites));
  if (zeroSites.length)   issues.appendChild(renderIssueList("零结果站点", zeroSites));
  if (failedFeeds.length) issues.appendChild(renderIssueList("失败 RSS", failedFeeds));
  if (skippedFeeds.length) {
    issues.appendChild(renderIssueList("跳过 RSS", skippedFeeds.map((item) => `${item.feed_url} · ${item.reason || "skipped"}`)));
  }
  if (issues.childElementCount) {
    sourceHealthEl.appendChild(issues);
  } else {
    const ok = document.createElement("div");
    ok.className = "health-ok";
    ok.textContent = "源状态正常";
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
    newsListEl.innerHTML = `<div class="empty-state">${newsResult.reason.message}</div>`;
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
    waytoagiListEl.innerHTML = `<div class="waytoagi-error">${waytoagiResult.reason?.message}</div>`;
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
    newsListEl.innerHTML = `<div class="empty-state">${err.message}</div>`;
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
  backToTopEl.classList.toggle("hidden", window.scrollY <= window.innerHeight * 2);
}, { passive: true });

backToTopEl.addEventListener("click", () => {
  window.scrollTo({ top: 0, behavior: "smooth" });
});

// ---- Launch -----------------------------------------------------------------

init();

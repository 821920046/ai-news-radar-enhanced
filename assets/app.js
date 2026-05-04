// ============================================================
//  AI Signal Board — app.js (redesigned)
//  Data-loading layer unchanged; rendering layer rebuilt around
//  time-based grouping with source-tinted card borders.
// ============================================================

// ---- global state -----------------------------------------------------------
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
  query: "",
  mode: "ai",
  sortBy: "time",  // "time" or "hot"
  waytoagiMode: "today",
  waytoagiData: null,
  sourceStatus: null,
  generatedAt: null,
};

// ---- DOM refs (all IDs preserved for backward-compat) -----------------------
const statsEl              = document.getElementById("stats");
const siteSelectEl         = document.getElementById("siteSelect");
const sitePillsEl          = document.getElementById("sitePills");
const newsListEl           = document.getElementById("newsList");
const updatedAtEl          = document.getElementById("updatedAt");
const searchInputEl        = document.getElementById("searchInput");
const resultCountEl        = document.getElementById("resultCount");
const listTitleEl          = document.getElementById("listTitle");
const itemTpl              = document.getElementById("itemTpl");
const modeAiBtnEl          = document.getElementById("modeAiBtn");
const modeAllBtnEl         = document.getElementById("modeAllBtn");
const modeHintEl           = document.getElementById("modeHint");
const allDedupeWrapEl      = document.getElementById("allDedupeWrap");
const allDedupeToggleEl    = document.getElementById("allDedupeToggle");
const allDedupeLabelEl     = document.getElementById("allDedupeLabel");
const advancedSummaryEl    = document.getElementById("advancedSummary");
const sourceHealthEl       = document.getElementById("sourceHealth");
const waytoagiUpdatedAtEl  = document.getElementById("waytoagiUpdatedAt");
const waytoagiMetaEl       = document.getElementById("waytoagiMeta");
const waytoagiListEl       = document.getElementById("waytoagiList");
const waytoagiTodayBtnEl   = document.getElementById("waytoagiTodayBtn");
const waytoagi7dBtnEl      = document.getElementById("waytoagi7dBtn");
const coverageStripEl      = document.getElementById("coverageStrip");
const backToTopEl          = document.getElementById("backToTop");
const sortTimeBtnEl        = document.getElementById("sortTimeBtn");
const sortHotBtnEl         = document.getElementById("sortHotBtn");

// ---- Source kind registry ---------------------------------------------------
// tone → CSS class suffix; label → badge text
const SOURCE_KINDS = {
  official_ai:  { label: "官方",   tone: "official" },
  aibreakfast:  { label: "日报",   tone: "newsletter" },
  followbuilders:{ label: "Builders/X", tone: "builders" },
  aihubtoday:   { label: "AI站点", tone: "aihub" },
  aibase:       { label: "AI站点", tone: "aihub" },
  techurls:     { label: "聚合",   tone: "aggregate" },
  buzzing:      { label: "聚合",   tone: "aggregate" },
  iris:         { label: "聚合",   tone: "aggregate" },
  bestblogs:    { label: "博客",   tone: "aggregate" },
  tophub:       { label: "聚合",   tone: "aggregate" },
  zeli:         { label: "聚合",   tone: "aggregate" },
  newsnow:      { label: "聚合",   tone: "aggregate" },
};

// ---- Utilities --------------------------------------------------------------

/** 大数字格式化（如 1,234） */
function fmtNumber(n) {
  return new Intl.NumberFormat("zh-CN").format(n || 0);
}

/** ISO → "05/04 14:30" */
function fmtTime(iso) {
  if (!iso) return "时间未知";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(d);
}

/** date string → "05/04" */
function fmtDate(iso) {
  if (!iso) return "未知日期";
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
  }).format(d);
}

/** 150ms 防抖 — 搜索输入时减少不必要的渲染 */
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
  return siteRows().find((site) => site.site_id === siteId) || null;
}

// ---- Time bucket helper -----------------------------------------------------
// 把 ISO 时间戳映射到 5 个时间桶：最近1h / 1-3h / 3-6h / 6-12h / 12-24h
const TIME_BUCKETS = [
  { label: "最近 1 小时", maxMs: 1 * 3600_000 },
  { label: "1-3 小时前", maxMs: 3 * 3600_000 },
  { label: "3-6 小时前", maxMs: 6 * 3600_000 },
  { label: "6-12 小时前", maxMs: 12 * 3600_000 },
  { label: "12-24 小时前", maxMs: 24 * 3600_000 },
];

function getTimeBucket(iso) {
  if (!iso) return 4; // 未知时间 → 放在最后一个桶
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 0) return 0; // 未来时间（时区偏移）→ 最新桶
  for (let i = 0; i < TIME_BUCKETS.length; i++) {
    if (diff < TIME_BUCKETS[i].maxMs) return i;
  }
  return TIME_BUCKETS.length - 1; // 超过24h → 最后一个桶
}

// ---- Stats (hidden but preserved) ------------------------------------------

function setStats(payload) {
  const cards = [
    ["AI 信号", fmtNumber(payload.total_items)],
    ["站点数",  fmtNumber(payload.site_count)],
    ["来源分组", fmtNumber(payload.source_count)],
    ["归档",   fmtNumber(payload.archive_total || 0)],
  ];
  statsEl.innerHTML = "";
  cards.forEach(([k, v]) => {
    const node = document.createElement("div");
    node.className = "stat";
    node.innerHTML = `<div class="k">${k}</div><div class="v">${v}</div>`;
    statsEl.appendChild(node);
  });
}

// ---- Coverage strip (hidden but preserved) ----------------------------------

function renderCoverageCard(label, value, meta, tone = "") {
  const node = document.createElement("div");
  node.className = `coverage-card ${tone}`.trim();
  const labelEl = document.createElement("span");
  labelEl.className = "coverage-label";
  labelEl.textContent = label;
  const valueEl = document.createElement("strong");
  valueEl.textContent = value;
  const metaEl = document.createElement("span");
  metaEl.className = "coverage-meta";
  metaEl.textContent = meta;
  node.append(labelEl, valueEl, metaEl);
  return node;
}

function renderCoverageStrip(errorMessage = "") {
  if (!coverageStripEl) return;
  coverageStripEl.innerHTML = "";

  const rows = siteRows();
  const failedSites = Array.isArray(state.sourceStatus?.failed_sites) ? state.sourceStatus.failed_sites : [];
  const rss = state.sourceStatus?.rss_opml || {};
  const allCount = Number(state.sourceStatus?.items_before_topic_filter || state.totalAllMode || state.itemsAll.length || 0);
  const coverageCount = Number(state.sourceStatus?.fetched_raw_items || state.totalRaw || allCount || 0);
  const officialCount = Number(siteRow("official_ai")?.item_count || 0);
  const newsletterCount = Number(siteRow("aibreakfast")?.item_count || 0);
  const buildersCount = Number(siteRow("followbuilders")?.item_count || 0);
  const totalSites = rows.length;
  const okSites = Number(state.sourceStatus?.successful_sites || 0);
  const opmlValue = rss.enabled
    ? `${fmtNumber(rss.ok_feeds || 0)}/${fmtNumber(rss.effective_feed_total || 0)}`
    : "OPML";
  const opmlMeta = rss.enabled ? "私有订阅已接入" : "可用 Secret 接入私有源";

  const cards = [
    ["源健康", totalSites ? `${fmtNumber(okSites)}/${fmtNumber(totalSites)}` : "加载中",
      failedSites.length ? `${fmtNumber(failedSites.length)} 个失败源` : (errorMessage || "内置源正常"),
      failedSites.length ? "warn" : "ok"],
    ["今日覆盖池", `${fmtNumber(coverageCount)} 条`,
      allCount ? `全网抓取原始信号 · ${fmtNumber(allCount)} 条入池` : "全网抓取原始信号", "signal"],
    ["AI精选", `${fmtNumber(state.totalAi)} 条`, "24小时强相关信号", "signal"],
    ["官方/日报源池", `${fmtNumber(officialCount + newsletterCount)} 条`, "官方节点 + AI Breakfast", "official"],
    ["Builders/X源池", `${fmtNumber(buildersCount)} 条`, "Follow Builders公开feed", "builders"],
    ["私人扩展", opmlValue, opmlMeta, "private"],
  ];
  cards.forEach(([label, value, meta, tone]) => {
    coverageStripEl.appendChild(renderCoverageCard(label, value, meta, tone));
  });
}

// ---- Advanced summary -------------------------------------------------------

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
  const totalSites = sites.length;
  const okSites = Number(status.successful_sites || 0);
  advancedSummaryEl.textContent = `${fmtNumber(okSites)}/${fmtNumber(totalSites)} 源可用 · 全量 ${fmtNumber(allCount)} 条`;
}

// ---- Site filters -----------------------------------------------------------

function computeSiteStats(items) {
  const m = new Map();
  items.forEach((item) => {
    if (!m.has(item.site_id)) {
      m.set(item.site_id, { site_id: item.site_id, site_name: item.site_name, count: 0, raw_count: 0 });
    }
    const row = m.get(item.site_id);
    row.count += 1;
    row.raw_count += 1;
  });
  return Array.from(m.values()).sort((a, b) => b.count - a.count || a.site_name.localeCompare(b.site_name, "zh-CN"));
}

function currentSiteStats() {
  if (state.mode === "ai") return state.statsAi || [];
  return computeSiteStats(state.allDedup ? (state.itemsAll || []) : (state.itemsAllRaw || []));
}

function renderSiteFilters() {
  const stats = currentSiteStats();

  // dropdown
  siteSelectEl.innerHTML = '<option value="">全部站点</option>';
  stats.forEach((s) => {
    const opt = document.createElement("option");
    opt.value = s.site_id;
    const raw = s.raw_count ?? s.count;
    opt.textContent = `${s.site_name} (${s.count}/${raw})`;
    siteSelectEl.appendChild(opt);
  });
  siteSelectEl.value = state.siteFilter;

  // pills
  sitePillsEl.innerHTML = "";
  const allPill = document.createElement("button");
  allPill.className = `pill ${state.siteFilter === "" ? "active" : ""}`;
  allPill.textContent = "全部";
  allPill.onclick = () => { state.siteFilter = ""; renderSiteFilters(); renderList(); };
  sitePillsEl.appendChild(allPill);

  stats.forEach((s) => {
    const btn = document.createElement("button");
    btn.className = `pill ${state.siteFilter === s.site_id ? "active" : ""}`;
    const raw = s.raw_count ?? s.count;
    btn.textContent = `${s.site_name} ${s.count}/${raw}`;
    btn.onclick = () => { state.siteFilter = s.site_id; renderSiteFilters(); renderList(); };
    sitePillsEl.appendChild(btn);
  });
}

// ---- Mode switch ------------------------------------------------------------

function renderModeSwitch() {
  modeAiBtnEl.classList.toggle("active", state.mode === "ai");
  modeAllBtnEl.classList.toggle("active", state.mode === "all");
  if (sortTimeBtnEl) sortTimeBtnEl.classList.toggle("active", state.sortBy === "time");
  if (sortHotBtnEl)  sortHotBtnEl.classList.toggle("active", state.sortBy === "hot");
  if (allDedupeWrapEl)   allDedupeWrapEl.classList.toggle("show", state.mode === "all");
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
    if (state.siteFilter && item.site_id !== state.siteFilter) return false;
    if (!q) return true;
    const hay = `${item.title || ""} ${item.title_zh || ""} ${item.title_en || ""} ${item.site_name || ""} ${item.source || ""}`.toLowerCase();
    return hay.includes(q);
  });

  // Sort by hotness if toggled
  if (state.sortBy === "hot") {
    items = [...items].sort((a, b) => {
      const sa = a.hotness_score || 0;
      const sb = b.hotness_score || 0;
      if (sb !== sa) return sb - sa;
      // Fallback: newest first
      return (b.published_at || "").localeCompare(a.published_at || "");
    });
  }

  return items;
}

// ---- Item rendering ---------------------------------------------------------

function renderItemNode(item) {
  const node = itemTpl.content.firstElementChild.cloneNode(true);
  const kind = sourceKind(item.site_id);

  // 左侧彩色边框 — 根据 tone 添加对应 class
  node.classList.add(`border-${kind.tone}`);

  node.querySelector(".site").textContent = item.site_name;

  const categoryEl = node.querySelector(".category");
  categoryEl.textContent = kind.label;
  categoryEl.classList.add(`kind-${kind.tone}`);

  node.querySelector(".source").textContent = `分区: ${item.source}`;
  node.querySelector(".time").textContent = fmtTime(item.published_at || item.first_seen_at);

  // 热度标签
  if (item.hotness_score > 0 && item.hotness_raw) {
    const badge = document.createElement("span");
    badge.className = "hotness-badge";
    badge.textContent = `🔥 ${item.hotness_raw}`;
    const metaRow = node.querySelector(".meta-row");
    metaRow.appendChild(badge);
  }

  const titleEl = node.querySelector(".title");
  const zh = (item.title_zh || "").trim();
  const en = (item.title_en || "").trim();
  titleEl.textContent = "";
  if (zh && en && zh !== en) {
    const primary = document.createElement("span");
    primary.textContent = zh;
    const sub = document.createElement("span");
    sub.className = "title-sub";
    sub.textContent = en;
    titleEl.appendChild(primary);
    titleEl.appendChild(sub);
  } else {
    titleEl.textContent = item.title || zh || en;
  }
  titleEl.href = item.url;
  return node;
}

// ---- Skeleton loading -------------------------------------------------------

function renderSkeleton(count = 5) {
  const frag = document.createDocumentFragment();
  for (let i = 0; i < count; i++) {
    const card = document.createElement("div");
    card.className = "skeleton-card";
    card.innerHTML = `
      <div class="flex items-center gap-2 mb-2.5">
        <div class="skeleton" style="width:60px;height:14px"></div>
        <div class="skeleton" style="width:36px;height:14px;border-radius:9999px"></div>
        <div class="skeleton ml-auto" style="width:72px;height:14px"></div>
      </div>
      <div class="skeleton" style="width:90%;height:16px"></div>
      <div class="skeleton mt-1.5" style="width:55%;height:12px"></div>
    `;
    frag.appendChild(card);
  }
  return frag;
}

// ---- Time-based grouping (default view) -------------------------------------

function renderTimeGrouped(items) {
  // 按时间桶分组
  const buckets = TIME_BUCKETS.map(() => []);
  items.forEach((item) => {
    const bucketIdx = getTimeBucket(item.published_at || item.first_seen_at);
    buckets[bucketIdx].push(item);
  });

  const frag = document.createDocumentFragment();

  buckets.forEach((bucketItems, idx) => {
    if (bucketItems.length === 0) return;

    // 组标题（sticky on mobile via CSS）
    const header = document.createElement("div");
    header.className = "time-group-head";
    const title = document.createElement("h3");
    title.textContent = TIME_BUCKETS[idx].label;
    const count = document.createElement("span");
    count.textContent = `${fmtNumber(bucketItems.length)} 条`;
    header.append(title, count);
    frag.appendChild(header);

    // 该桶内的卡片
    bucketItems.forEach((item) => frag.appendChild(renderItemNode(item)));
  });

  newsListEl.appendChild(frag);
}

// ---- Source-grouped view (when site filter is active) -----------------------

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

function buildSourceGroupNode(source, items) {
  const section = document.createElement("section");
  section.className = "source-group";
  const header = document.createElement("header");
  header.className = "source-group-head";
  const title = document.createElement("h3");
  title.textContent = source;
  const count = document.createElement("span");
  count.textContent = `${fmtNumber(items.length)} 条`;
  const listEl = document.createElement("div");
  listEl.className = "source-group-list";
  header.append(title, count);
  section.append(header, listEl);
  items.forEach((item) => listEl.appendChild(renderItemNode(item)));
  return section;
}

function renderGroupedBySource(items) {
  const groups = groupBySource(items);
  const frag = document.createDocumentFragment();
  groups.forEach(([source, groupItems]) => {
    frag.appendChild(buildSourceGroupNode(source, groupItems));
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
    empty.className = "empty";
    empty.textContent = "当前筛选条件下没有结果。";
    newsListEl.appendChild(empty);
    return;
  }

  if (state.siteFilter) {
    // 选了具体站点 → 按来源分组
    renderGroupedBySource(filtered);
  } else {
    // 默认 → 按时间桶分组
    renderTimeGrouped(filtered);
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
  if (waytoagiTodayBtnEl) waytoagiTodayBtnEl.classList.toggle("active", state.waytoagiMode === "today");
  if (waytoagi7dBtnEl)    waytoagi7dBtnEl.classList.toggle("active", state.waytoagiMode === "7d");
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

// ---- Source health -----------------------------------------------------------

function renderMetric(label, value, tone = "") {
  const node = document.createElement("div");
  node.className = `health-metric ${tone}`.trim();
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

// ---- Data loaders -----------------------------------------------------------

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
  // 显示骨架屏
  newsListEl.innerHTML = "";
  newsListEl.appendChild(renderSkeleton(5));

  const [newsResult, waytoagiResult, statusResult] = await Promise.allSettled([
    loadNewsData(),
    loadWaytoagiData(),
    loadSourceStatusData(),
  ]);

  if (newsResult.status === "fulfilled") {
    const payload = newsResult.value;
    state.itemsAi      = payload.items_ai || payload.items || [];
    state.itemsAllRaw  = payload.items_all_raw || payload.items_all || [];
    state.itemsAll     = payload.items_all || [];
    state.statsAi      = payload.site_stats || [];
    state.totalAi      = payload.total_items || state.itemsAi.length;
    state.totalRaw     = payload.total_items_raw || state.itemsAllRaw.length;
    state.totalAllMode = payload.total_items_all_mode || state.itemsAll.length;
    state.allDataUrl   = payload.all_mode_data_url || state.allDataUrl;
    state.allDataLoaded = Boolean(payload.items_all || payload.items_all_raw);
    state.generatedAt  = payload.generated_at;

    setStats(payload);
    renderModeSwitch();
    renderCoverageStrip();
    renderSiteFilters();
    renderList();
    updatedAtEl.textContent = `更新时间：${fmtTime(state.generatedAt)}`;
  } else {
    updatedAtEl.textContent = "新闻数据加载失败";
    newsListEl.innerHTML = `<div class="empty">${newsResult.reason.message}</div>`;
    renderCoverageStrip(newsResult.reason.message);
  }

  if (statusResult.status === "fulfilled") {
    state.sourceStatus = statusResult.value;
    renderSourceHealth();
    renderCoverageStrip();
  } else {
    renderSourceHealth(statusResult.reason.message);
    renderCoverageStrip(statusResult.reason.message);
  }

  if (waytoagiResult.status === "fulfilled") {
    state.waytoagiData = waytoagiResult.value;
    renderWaytoagi(state.waytoagiData);
  } else {
    waytoagiUpdatedAtEl.textContent = "加载失败";
    waytoagiListEl.innerHTML = `<div class="waytoagi-error">${waytoagiResult.reason.message}</div>`;
  }
}

// ---- Event listeners --------------------------------------------------------

// 搜索框 — 150ms 防抖
const debouncedSearch = debounce((value) => {
  state.query = value;
  renderList();
}, 150);

searchInputEl.addEventListener("input", (e) => {
  debouncedSearch(e.target.value);
});

// Esc 键清空搜索并失焦
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
  renderSiteFilters();
  renderList();
});

modeAllBtnEl.addEventListener("click", async () => {
  state.mode = "all";
  renderModeSwitch();
  // 切换到全量模式时显示骨架屏
  newsListEl.innerHTML = "";
  newsListEl.appendChild(renderSkeleton(5));
  try {
    await loadAllModeData();
    renderSiteFilters();
    renderList();
  } catch (err) {
    newsListEl.innerHTML = `<div class="empty">${err.message}</div>`;
  }
});

if (allDedupeToggleEl) {
  allDedupeToggleEl.addEventListener("change", (e) => {
    state.allDedup = Boolean(e.target.checked);
    renderModeSwitch();
    renderSiteFilters();
    renderList();
  });
}

if (sortTimeBtnEl) {
  sortTimeBtnEl.addEventListener("click", () => {
    state.sortBy = "time";
    renderModeSwitch();
    renderList();
  });
}

if (sortHotBtnEl) {
  sortHotBtnEl.addEventListener("click", () => {
    state.sortBy = "hot";
    renderModeSwitch();
    renderList();
  });
}

if (waytoagiTodayBtnEl) {
  waytoagiTodayBtnEl.addEventListener("click", () => {
    state.waytoagiMode = "today";
    if (state.waytoagiData) renderWaytoagi(state.waytoagiData);
  });
}

if (waytoagi7dBtnEl) {
  waytoagi7dBtnEl.addEventListener("click", () => {
    state.waytoagiMode = "7d";
    if (state.waytoagiData) renderWaytoagi(state.waytoagiData);
  });
}

// ---- Back-to-top button -----------------------------------------------------

window.addEventListener("scroll", () => {
  if (window.scrollY > window.innerHeight * 2) {
    backToTopEl.classList.remove("hidden");
  } else {
    backToTopEl.classList.add("hidden");
  }
}, { passive: true });

backToTopEl.addEventListener("click", () => {
  window.scrollTo({ top: 0, behavior: "smooth" });
});

// ---- Launch -----------------------------------------------------------------

init();

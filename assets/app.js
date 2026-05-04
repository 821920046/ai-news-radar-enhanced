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
  waytoagiData: null,
  sourceStatus: null,
  generatedAt: null,
};

// 仪表盘实例
let captureChart = null;
let healthChart = null;

const newsListEl = document.getElementById("news-container");
const updatedAtEl = document.getElementById("last-update-time");
const sourceCoverageEl = document.getElementById("source-coverage-list");
const waytoagiListEl = document.getElementById("waytoagi-list");
const totalCountEl = document.getElementById("stat-total-count");
const healthPercentEl = document.getElementById("stat-health-percent");
const nodeCountEl = document.getElementById("stat-source-count-node");
const mobileStatEl = document.getElementById("stat-mobile");

const CHART_COLORS = {
  cyan: {
    solid: '#22d3ee',
    faded: 'rgba(34, 211, 238, 0.1)',
    glow: 'rgba(34, 211, 238, 0.5)'
  },
  purple: {
    solid: '#c084fc',
    faded: 'rgba(192, 132, 252, 0.1)',
    glow: 'rgba(192, 132, 252, 0.5)'
  },
  amber: {
    solid: '#fbbf24',
    faded: 'rgba(251, 191, 36, 0.1)'
  }
};

function initGauges() {
  const commonOptions = {
    cutout: '85%',
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: false }, tooltip: { enabled: false } },
    animation: { duration: 2000, easing: 'easeOutQuart' }
  };

  const canvasCapture = document.getElementById('gauge-capture');
  if (canvasCapture) {
    const ctxCapture = canvasCapture.getContext('2d');
    captureChart = new Chart(ctxCapture, {
      type: 'doughnut',
      data: {
        datasets: [{
          data: [0, 100],
          backgroundColor: [CHART_COLORS.cyan.solid, CHART_COLORS.cyan.faded],
          borderWidth: 0,
          borderRadius: 10
        }]
      },
      options: commonOptions
    });
  }

  const canvasHealth = document.getElementById('gauge-health');
  if (canvasHealth) {
    const ctxHealth = canvasHealth.getContext('2d');
    healthChart = new Chart(ctxHealth, {
      type: 'doughnut',
      data: {
        datasets: [{
          data: [0, 100],
          backgroundColor: [CHART_COLORS.purple.solid, CHART_COLORS.purple.faded],
          borderWidth: 0,
          borderRadius: 10
        }]
      },
      options: commonOptions
    });
  }
}

function updateGauges() {
  if (!captureChart || !healthChart) return;

  // 1. 更新信号捕获盘 (展示 AI 信号占 24H 预期满载的比例，假设基准为 200 条)
  const captureTarget = 200;
  const captureVal = Math.min(state.totalAi, captureTarget);
  captureChart.data.datasets[0].data = [captureVal, Math.max(0, captureTarget - captureVal)];
  captureChart.update();
  if (totalCountEl) totalCountEl.textContent = state.totalAi;

  // 2. 更新健康度盘
  if (state.sourceStatus) {
    const total = state.sourceStatus.sites?.length || 0;
    const ok = state.sourceStatus.successful_sites || 0;
    const percent = total > 0 ? Math.round((ok / total) * 100) : 0;
    
    healthChart.data.datasets[0].data = [ok, Math.max(0, total - ok)];
    healthChart.data.datasets[0].backgroundColor = [
      percent > 80 ? CHART_COLORS.purple.solid : CHART_COLORS.amber.solid,
      CHART_COLORS.purple.faded
    ];
    healthChart.update();

    if (healthPercentEl) healthPercentEl.textContent = `${percent}%`;
    if (nodeCountEl) nodeCountEl.textContent = `${ok}/${total} Nodes`;
    if (mobileStatEl) mobileStatEl.textContent = `${percent}% UPLINK OK`;
  }
}

function fmtTime(iso) {
  if (!iso) return "--:--";
  const d = new Date(iso);
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false });
}

function fmtDate(iso) {
  if (!iso) return "Unknown";
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

function renderItemNode(item) {
  const node = document.createElement("article");
  node.className = "data-card glass-panel rounded-2xl p-5 border-l-4 border-cyan-500/50 relative overflow-hidden group cursor-pointer";
  
  const isOfficial = item.site_id === 'official_ai';
  if (isOfficial) node.classList.replace('border-cyan-500/50', 'border-purple-500/50');

  const title = item.title_zh || item.title;
  const subTitle = item.title_zh ? item.title : '';

  node.innerHTML = `
    <div class="flex items-center justify-between mb-3 relative z-10">
      <div class="flex items-center gap-2">
        <span class="px-2 py-0.5 rounded-md bg-white/5 text-[9px] mono-font text-white/50 uppercase tracking-wider border border-white/5 group-hover:border-cyan-500/30 transition-colors">${item.site_name}</span>
        <span class="text-[10px] mono-font text-cyan-400/40 uppercase group-hover:text-cyan-400/80 transition-colors">${item.source || 'ROOT'}</span>
      </div>
      <time class="text-[10px] mono-font text-white/20 group-hover:text-white/40 transition-colors">${fmtTime(item.published_at || item.first_seen_at)}</time>
    </div>
    <a href="${item.url}" target="_blank" class="block relative z-10">
      <h4 class="text-sm md:text-base font-semibold text-white/90 leading-relaxed group-hover:text-cyan-400 transition-colors mb-1 heading-font">${title}</h4>
      ${subTitle ? `<p class="text-[11px] text-white/30 line-clamp-1 italic font-light">${subTitle}</p>` : ''}
    </a>
    <div class="absolute -right-4 -bottom-4 w-24 h-24 bg-gradient-to-br from-cyan-500/5 to-transparent rounded-full blur-2xl opacity-0 group-hover:opacity-100 transition-opacity"></div>
  `;
  return node;
}

function renderCoverageCard(site) {
  const isOk = !state.sourceStatus?.failed_sites?.includes(site.site_id);
  const node = document.createElement("div");
  node.className = "flex items-center justify-between p-3 rounded-xl bg-white/[0.02] border border-white/5 hover:bg-white/[0.05] transition-all group";
  node.innerHTML = `
    <div class="flex items-center gap-3">
      <div class="w-1.5 h-1.5 rounded-full ${isOk ? 'bg-cyan-500 shadow-[0_0_8px_rgba(34,211,238,0.5)]' : 'bg-amber-500 animate-pulse'}"></div>
      <span class="text-xs font-medium text-white/60 group-hover:text-white/90 transition-colors">${site.site_name}</span>
    </div>
    <span class="text-[10px] mono-font text-white/20">${site.item_count || 0}</span>
  `;
  return node;
}

function renderList() {
  if (!newsListEl) return;
  newsListEl.innerHTML = "";
  const items = state.mode === 'ai' ? state.itemsAi : (state.allDedup ? state.itemsAll : state.itemsAllRaw);
  
  if (!items || !items.length) {
    newsListEl.innerHTML = `<div class="p-20 text-center text-white/10 uppercase tracking-widest text-[10px] mono-font">No Signal Detected</div>`;
    return;
  }

  const frag = document.createDocumentFragment();
  items.slice(0, 50).forEach(item => frag.appendChild(renderItemNode(item)));
  newsListEl.appendChild(frag);
}

function renderWaytoagi(waytoagi) {
  if (!waytoagiListEl) return;
  waytoagiListEl.innerHTML = "";
  const updates = (waytoagi.updates_7d || []).slice(0, 8);
  
  updates.forEach(u => {
    const node = document.createElement("div");
    node.className = "relative pl-5 pb-5 border-l border-white/5 last:pb-0 group";
    node.innerHTML = `
      <div class="absolute -left-[3.5px] top-1.5 w-1.5 h-1.5 rounded-full bg-white/10 group-hover:bg-cyan-500 transition-colors shadow-lg"></div>
      <div class="text-[9px] text-white/20 mono-font mb-1 uppercase">${fmtDate(u.date)}</div>
      <a href="${u.url || '#'}" target="_blank" class="text-[11px] font-medium text-white/50 hover:text-cyan-400 transition-colors line-clamp-2 leading-relaxed">
        ${u.title}
      </a>
    `;
    waytoagiListEl.appendChild(node);
  });
}

async function loadData() {
  try {
    const [newsRes, waytoagiRes, statusRes] = await Promise.all([
      fetch(`./data/latest-24h.json?t=${Date.now()}`),
      fetch(`./data/waytoagi-7d.json?t=${Date.now()}`),
      fetch(`./data/source-status.json?t=${Date.now()}`)
    ]);

    const news = await newsRes.json();
    const waytoagi = await waytoagiRes.json();
    const status = await statusRes.json();

    state.itemsAi = news.items_ai || news.items || [];
    state.totalAi = news.total_items || state.itemsAi.length;
    state.generatedAt = news.generated_at;
    state.sourceStatus = status;

    if (updatedAtEl) updatedAtEl.textContent = `SYNCED AT ${fmtTime(state.generatedAt)}`;
    
    renderList();
    renderWaytoagi(waytoagi);
    
    // 渲染源列表
    if (sourceCoverageEl && status.sites) {
      sourceCoverageEl.innerHTML = "";
      status.sites.slice(0, 15).forEach(site => sourceCoverageEl.appendChild(renderCoverageCard(site)));
    }

    updateGauges();
  } catch (err) {
    console.error("Data Sync Failed:", err);
    if (updatedAtEl) updatedAtEl.textContent = "SYNC FAILED";
  }
}

// 模式切换
const toggleBtn = document.getElementById("toggle-all-mode");
if (toggleBtn) {
  toggleBtn.addEventListener("click", async () => {
    state.mode = state.mode === 'ai' ? 'all' : 'ai';
    toggleBtn.textContent = state.mode === 'ai' ? 'VERBOSE' : 'AI SIGNAL';
    toggleBtn.classList.toggle('bg-cyan-500/10', state.mode === 'all');
    toggleBtn.classList.toggle('text-cyan-400', state.mode === 'all');
    
    if (state.mode === 'all' && !state.allDataLoaded) {
       newsListEl.innerHTML = `<div class="p-20 text-center animate-pulse text-cyan-400/20 mono-font text-[10px] tracking-[0.3em]">RE-ROUTING DATA STREAM...</div>`;
       try {
         const res = await fetch(`./data/latest-24h-all.json?t=${Date.now()}`).then(r => r.json());
         state.itemsAll = res.items_all || [];
         state.itemsAllRaw = res.items_all_raw || [];
         state.allDataLoaded = true;
       } catch (e) {
         console.error("Load all data failed", e);
       }
    }
    renderList();
    updateGauges();
  });
}

// 初始化
window.addEventListener('DOMContentLoaded', () => {
  initGauges();
  loadData();
});

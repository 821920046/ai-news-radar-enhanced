# AI Signal Board — 24 小时 AI 资讯雷达（优化版）

> 多源聚合 · 智能信号评分（S/A/B/C）· AI 摘要 · 构建期静态预渲染（SEO 友好）· 每小时自动更新

本仓库在原 `ai-news-radar-enhanced (v3)` 基础上做了一批工程优化，本文档同时给出**完整的部署方案**。

---

## 目录

- [一、功能概览](#一功能概览)
- [二、本次优化内容](#二本次优化内容)
- [三、目录结构](#三目录结构)
- [四、环境要求](#四环境要求)
- [五、本地开发部署](#五本地开发部署)
- [六、数据生成与静态预渲染](#六数据生成与静态预渲染)
- [七、前端静态站点部署](#七前端静态站点部署)
  - [7.1 GitHub Pages](#71-github-pages)
  - [7.2 Vercel / Netlify / Cloudflare Pages](#72-vercel--netlify--cloudflare-pages)
  - [7.3 自有服务器 + Nginx](#73-自有服务器--nginx)
- [八、API 服务部署](#八api-服务部署)
  - [8.1 直接运行 / systemd](#81-直接运行--systemd)
  - [8.2 Docker / Docker Compose](#82-docker--docker-compose)
- [九、GitHub Actions 自动化](#九github-actions-自动化)
- [十、环境变量清单](#十环境变量清单)
- [十一、配置评分引擎](#十一配置评分引擎)
- [十二、健康检查与监控](#十二健康检查与监控)
- [十三、常见问题排查](#十三常见问题排查)
- [十四、安全须知](#十四安全须知)

---

## 一、功能概览

| 模块 | 能力 |
|---|---|
| 数据采集 | 50+ 内置 AI/科技信源（OpenAI、DeepMind、Anthropic、HuggingFace、arXiv、量子位、机器之心…）+ 私有 OPML 订阅 |
| 处理管线 | 采集 → 归一化 → 去重合并 → AI 相关性过滤 → 信号评分 → AI 增强 → 输出快照 |
| 信号评分 | Signal Score 2.0：信源权威/技术深度/新颖度/传播速度/社区信号 5 维加权，S/A/B/C 分层 |
| AI 增强 | TLDR 一句话摘要、标题中文翻译（带缓存）、推荐理由 |
| API | `/health` `/daily-report` `/daily-report/markdown` `/trends` `/items` `/stats` |
| 前端 | AI 强相关/全量切换、时间序/热度榜、高级筛选、源站点检索、采集源健康面板、WaytoAGI 更新日志 |
| 自动化 | GitHub Actions 每小时定时、数据质量校验、Webhook 告警、历史归档 |

---

## 二、本次优化内容

| 文件 | 类型 | 说明 |
|---|---|---|
| `config/score_weights.yaml` | 新增 | 评分权重/阈值/信源权威度全部外置，改配置即可调参，无需改代码 |
| `core/signal_score/scorer.py` | 重写 | 配置驱动；权重自动归一化；新增 percentile 动态分级；批量评分输出分布日志 |
| `api/app.py` | 重写 | JSON 读取内存缓存（mtime 失效）；CORS 收紧；修复 `datetime.utcnow()`；`/health` 增加数据新鲜度；`/trends` 支持趋势引擎产出优先 |
| `scripts/prerender.py` | 新增 | **构建期静态预渲染（SSG）**，解决纯客户端 SPA 的首屏白屏 + SEO 不可收录问题；幂等可重复运行 |
| `tests/test_signal_score.py` | 新增 | 评分/分级/归一化/percentile 单元测试 |
| `.github/workflows/update-news.yml` | 更新 | 在采集后新增预渲染步骤并提交 `index.html`；启用失败 Webhook 告警 |
| `examples/hot-ticker/hot-ticker.js` | 新增 | 顶部「24h 最热新闻 + 最热开源」实时滚动榜组件（零依赖、点击查看详情、防 XSS） |
| `examples/hot-ticker/demo.html` | 新增 | 热榜组件独立演示页（内置示例数据） |
| `examples/hot-ticker/INTEGRATION.md` | 新增 | 顶部热榜区集成说明 |
| `api/app.py` `/hot` | 新增接口 | 返回 `news` / `opensource` 两个 24h 热榜（供前端滚动榜调用） |

---

## 三、目录结构

```text
ai-news-radar-enhanced/
├── api/
│   └── app.py                    # FastAPI 应用入口
├── core/
│   ├── models.py                 # 信源清单与全局常量
│   ├── utils.py                  # compute_hotness / strip_html_tags 等
│   ├── pipeline/                 # 七阶段处理管线
│   └── signal_score/
│       ├── scorer.py             # 评分引擎（本次重写）
│       └── features.py           # 新颖度/传播速度/社区信号特征
├── config/                        # 全部配置集中在这里
│   ├── sources.yaml              # 信源配置
│   ├── score_weights.yaml        # 评分权重 / 阈值 / 信源权威度
│   ├── model_config.yaml         # OpenRouter 模型配置
│   └── topic_rules.json          # 主题分类关键词规则
├── scripts/                       # 只放可执行入口
│   ├── update_news.py            # 数据更新入口（CI 调用）
│   ├── prerender.py              # 静态预渲染
│   └── probe_aihot.py            # 信源探测工具
├── tools/ci/                      # CI 运维脚本
│   ├── cleanup_artifacts.sh      # 定时清理 Actions artifact
│   └── retry_transient_failure.sh # 瞬时故障自动重跑判定
├── data/                         # 只放前端会 fetch 的 5 个 JSON
│   ├── latest-24h.json
│   ├── latest-24h-all.json
│   ├── source-status.json
│   ├── waytoagi-7d.json
│   └── trends.json
│   # archive.json / trend_history.json / title-zh-cache.json 为管道状态，
│   # 由 update-news.yml 在 pipeline-state 分支上维护，不进主干
├── docs/                          # history/ 历史记录，maintenance/ 运维诊断
├── examples/hot-ticker/           # 独立示例组件（主站不引用）
├── tests/                         # 业务单测；tests/ci/ 为 CI 脚本测试替身
├── index.html                    # 前端入口（GitHub Pages 发布，预渲染目标）
├── requirements.txt
└── .github/workflows/update-news.yml
```

> ⚠️ `index.html` 的实际位置取决于你的 GitHub Pages 发布设置（仓库根 / `docs/` / `gh-pages` 分支）。预渲染脚本的 `--html` 参数要指向这个真实文件。

---

## 四、环境要求

- **Python** 3.11+（CI 使用 3.11，本地 3.11–3.13 均可）
- **pip** / 虚拟环境（venv 推荐）
- 可选：**Docker** 20+、**Node**（仅当你的前端有构建步骤时）
- 可选：**OpenRouter API Key**（启用 AI TLDR 摘要时需要）

---

## 五、本地开发部署

```bash
# 1) 克隆
git clone https://github.com/821920046/ai-news-radar-enhanced.git
cd ai-news-radar-enhanced

# 2) 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3) 安装依赖
pip install --upgrade pip
pip install -r requirements.txt

# 4) 跑测试（确认环境正常）
python -m pytest tests/ -v

# 5) 首次生成数据快照
python scripts/update_news.py --output-dir data --window-hours 24 --archive-days 3

# 6) 启动 API（开发模式，热重载）
uvicorn api.app:app --host 0.0.0.0 --port 8000 --reload
```

打开 http://localhost:8000/health 确认服务正常，http://localhost:8000/docs 查看自动生成的 OpenAPI 文档。

---

## 六、数据生成与静态预渲染

```bash
# 生成 / 刷新 24 小时快照
python scripts/update_news.py --output-dir data --window-hours 24 --archive-days 3

# 启用私有 OPML 订阅（可选）
python scripts/update_news.py --output-dir data --window-hours 24 --archive-days 3 \
  --rss-opml feeds/follow.opml

# 构建期静态预渲染：把首屏新闻 + SEO 元信息写进 index.html
python scripts/prerender.py --data data/latest-24h.json --html index.html --top 30

# 可选：指定 Open Graph 配图 / 输出到其他文件
python scripts/prerender.py --data data/latest-24h.json --html index.html \
  --out dist/index.html --top 30 --og-image https://news.my-tv.eu.cc/og.png
```

**预渲染做了什么**：
1. 注入真实 `<title>` / `description` / Open Graph / Twitter Card / JSON-LD `ItemList`（搜索引擎与分享卡片可读）。
2. 内联首屏数据为 `<script id="__PRERENDER_DATA__" type="application/json">`，前端可直接读取，省去首屏一次 fetch。
3. 在 `#prerender-root` 容器输出服务端渲染的新闻列表，首屏无需等待 JS。

脚本**幂等**：通过 HTML 注释标记定位注入区，可被每小时 CI 重复调用而不会累积重复内容。首次运行若模板里没有标记，会自动在 `</head>` 前与 `<body>` 后插入。

**前端配合（可选但推荐）**，在你的 `app.js` 初始化处加入：

```js
// 优先使用预渲染内联数据，避免首屏再发一次 fetch
function getInitialData() {
  const el = document.getElementById("__PRERENDER_DATA__");
  if (el) {
    try { return JSON.parse(el.textContent); } catch (_) {}
  }
  return null;
}
// 客户端渲染完成后，移除服务端占位列表
function clearPrerender() {
  const root = document.getElementById("prerender-root");
  if (root) root.remove();
}
```

---

## 七、前端静态站点部署

前端是纯静态资源（`index.html` + JS/CSS + `data/*.json`），任意静态托管均可。

### 7.1 GitHub Pages

1. 仓库 **Settings → Pages**，Source 选择 `Deploy from a branch`。
2. 选择分支与目录（仓库根 `/` 或 `/docs`）。
3. 每小时 Actions 会自动更新 `data/*.json` 与预渲染后的 `index.html` 并 push，Pages 自动重新发布。
4. 自定义域名：在仓库根放 `CNAME` 文件（内容为你的域名，如 `news.my-tv.eu.cc`），并在 DNS 配置 CNAME 记录指向 `<user>.github.io`。

> 当前部署：https://news.my-tv.eu.cc/ 。务必确认 Workflow 的 `--html` 路径与 Pages 发布目录一致。

### 7.2 Vercel / Netlify / Cloudflare Pages

- **Build command**：留空（无需构建）或 `python scripts/prerender.py --data data/latest-24h.json --html index.html`。
- **Output directory**：仓库根（或 `dist/`，与 `--out` 对应）。
- 数据刷新仍由 GitHub Actions 负责 push，平台监听到 push 后自动重新部署。

### 7.3 自有服务器 + Nginx

```nginx
server {
    listen 80;
    server_name news.example.com;
    root /var/www/ai-news-radar;          # 放置 index.html 与 data/ 的目录
    index index.html;

    # 数据 JSON：缩短缓存，保证新鲜
    location /data/ {
        add_header Cache-Control "public, max-age=300";
    }

    # 反向代理 API（如同机部署）
    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

用 cron 定时拉取最新代码 / 数据：

```bash
# crontab -e ，每小时第 5 分钟同步
5 * * * * cd /var/www/ai-news-radar && git pull --quiet
```

---

## 八、API 服务部署

> API 是可选的：纯前端站点直接读取 `data/*.json` 即可运行。需要 `/items` 过滤、`/trends`、`/stats` 等动态接口时再部署 API。

### 8.1 直接运行 / systemd

```bash
pip install uvicorn[standard]
uvicorn api.app:app --host 0.0.0.0 --port 8000 --workers 2
```

`/etc/systemd/system/ai-news-api.service`：

```ini
[Unit]
Description=AI News Radar API
After=network.target

[Service]
WorkingDirectory=/opt/ai-news-radar-enhanced
Environment="ALLOW_ORIGINS=https://news.my-tv.eu.cc"
Environment="DATA_STALE_HOURS=6"
ExecStart=/opt/ai-news-radar-enhanced/.venv/bin/uvicorn api.app:app --host 0.0.0.0 --port 8000 --workers 2
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ai-news-api
sudo systemctl status ai-news-api
```

### 8.2 Docker / Docker Compose

`Dockerfile`：

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt uvicorn[standard]
COPY . .
EXPOSE 8000
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

`docker-compose.yml`：

```yaml
services:
  api:
    build: .
    ports:
      - "8000:8000"
    environment:
      ALLOW_ORIGINS: "https://news.my-tv.eu.cc"
      DATA_STALE_HOURS: "6"
    volumes:
      - ./data:/app/data:ro          # 挂载数据目录（只读）
    restart: unless-stopped
```

```bash
docker compose up -d --build
curl http://localhost:8000/health
```

---

## 九、GitHub Actions 自动化

工作流 `.github/workflows/update-news.yml` 每小时执行：

1. Checkout → 安装依赖 → 跑 `pytest`
2. （可选）从 `FOLLOW_OPML_B64` 解码私有 OPML
3. `scripts/update_news.py` 采集并生成 `data/*.json`
4. **数据质量校验**：条目数 < 3 直接失败
5. **`scripts/prerender.py` 预渲染** `index.html`（本次新增）
6. Commit & push `data/*.json` + `index.html`
7. 失败时 Webhook 告警（需配置 `WEBHOOK_URL`）

**启用步骤**：
1. 仓库 **Settings → Secrets and variables → Actions** 配置所需 secret（见下表）。
2. **Settings → Actions → General → Workflow permissions** 选 **Read and write permissions**（CI 需要 push）。
3. 手动触发一次：**Actions → Update AI News Snapshot → Run workflow**，确认全绿。

---

## 十、环境变量清单

| 变量 | 作用 | 默认 / 示例 |
|---|---|---|
| `ALLOW_ORIGINS` | API 允许的跨域来源（逗号分隔） | `*`；建议生产填 `https://news.my-tv.eu.cc` |
| `DATA_STALE_HOURS` | `/health` 判定数据过期的小时阈值 | `6` |
| `OPENROUTER_KEYS` | OpenRouter API Key（可多个，逗号分隔） | *secret* |
| `OPENROUTER_MODEL` | TLDR 使用的模型 | 如 `google/gemini-flash-1.5` |
| `AI_TLDR_ENABLED` | 是否启用 AI 摘要 | `true` / `false` |
| `AI_TLDR_TOP_N` | 仅对前 N 条生成摘要 | `30` |
| `AI_TLDR_MAX_WORKERS` | 摘要并发数 | `4` |
| `WEBHOOK_URL` | 失败/热点通知地址 | *secret* |
| `WEBHOOK_TYPE` | webhook 类型 | `feishu` / `slack` / `generic` |
| `WEBHOOK_MODE` | 通知模式 | 视实现而定 |
| `WEBHOOK_HOTNESS_THRESHOLD` | 热点推送阈值 | 数值 |
| `FOLLOW_OPML_B64` | 私有 OPML（base64） | *secret* |

> 本地可在项目根放 `.env` 并配合 `python-dotenv` 读取；CI 中通过 Actions Secrets 注入。**切勿把 Key 提交进仓库。**

---

## 十一、配置评分引擎

编辑 `config/score_weights.yaml`，无需改代码即可调参：

```yaml
weights:               # 5 维权重（引擎自动归一化）
  source_weight: 0.25
  technical_score: 0.25
  novelty: 0.20
  velocity: 0.15
  community: 0.15

levels:
  mode: "static"       # static=固定阈值；percentile=动态分位数分级
  thresholds: { S: 85, A: 70, B: 50 }
  percentiles: { S: 0.90, A: 0.70, B: 0.40 }

source_authority:      # site_id -> 0-100
  official_ai: 100
  aibreakfast: 85
  # ...
```

- 想缓解分数通胀导致「全是 C / 全是 S」，把 `levels.mode` 改成 `percentile`。
- 批量评分后会打印 `[SignalScore] mode=... distribution={S,A,B,C}` 日志，便于观察分级是否健康。

---

## 十二、健康检查与监控

```bash
curl -s http://localhost:8000/health | jq
```

返回示例：

```json
{
  "status": "ok",
  "version": "3.1.0",
  "data_age_hours": 0.8,
  "stale": false,
  "total_items": 42,
  "successful_sources": 38,
  "failed_sources": ["量子位"]
}
```

- `stale: true` 表示数据超过 `DATA_STALE_HOURS` 未更新，应排查 Actions 是否失败。
- `failed_sources` 列出本轮抓取失败的源，可用于告警。
- 可用 UptimeRobot / Healthchecks.io 定时探测 `/health` 并对 `stale` 报警。

---

## 十三、常见问题排查

| 现象 | 排查 |
|---|---|
| 站点首屏一直「加载中…/0 条」 | 确认已运行 `prerender.py` 且 `--html` 指向真实发布的 `index.html`；检查 `data/latest-24h.json` 是否存在且非空 |
| 搜索引擎抓不到内容 | 同上；查看页面源码是否含 `og:`、`application/ld+json` 与 `#prerender-root` 列表 |
| API 跨域报错 | 设置 `ALLOW_ORIGINS` 为你的前端域名；浏览器控制台看 CORS 报错来源 |
| Actions 失败：`Too few items` | 多个源失败导致条目 < 3；查看 `source-status.json` 与运行日志，必要时调整源清单 |
| `pytest` 报缺少模块 | 确认已 `pip install -r requirements.txt`，且包含 `pytest` 与 `pyyaml` |
| TLDR 摘要为空 | 检查 `AI_TLDR_ENABLED=true` 与 `OPENROUTER_KEYS` 是否配置、额度是否用尽 |
| push 被拒 | Actions 权限改为 Read and write；分支保护规则放行 `github-actions[bot]` |

---

## 十四、安全须知

- **清理提示注入**：原 `README` / `SKILL.md` 中面向 AI Agent 的「交接提示词」属于 prompt injection，应删除，切勿让自动化代理执行。
- **XSS 防护**：前端渲染第三方标题/摘要时必须做 HTML 转义；`prerender.py` 已用 `html.escape` 处理注入内容。
- **密钥管理**：所有 Key 通过环境变量 / Actions Secrets 注入，不入库；定期轮换 OpenRouter Key。
- **CORS**：生产环境务必把 `ALLOW_ORIGINS` 收紧为你的域名，不要长期使用 `*`。

---

_AI Signal Board · 优化版 v3.1.0 · 文档随代码包一同发布_

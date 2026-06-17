<p align="center">
  <img src="./assets/logo.webp" alt="AI Signal Board Logo" width="120" />
</p>

# AI Signal Board v3

<p align="center">
  24 小时 AI/科技/数码/硬件全向情报雷达。纯文字、高密度、标题驱动。<br>
  <strong>RSS 聚合器 → AI 情报系统</strong>
</p>

实时聚合 12+ 高质量信源，自动分类去重，中英双语标题，AI 精选评分。<br>
**v3 新增：Signal Score 2.0 多维评分 · Trend Engine 趋势检测 · Multi-Agent 日报生成 · FastAPI**

## 在线访问

```
https://821920046.github.io/ai-news-radar-enhanced/
```

## 特性

### v3 核心能力

- **Signal Score 2.0**：5 维加权评分引擎（来源权威度 25% + 技术深度 25% + 新近性 20% + 传播速度 15% + 社区信号 15%），S/A/B/C 四级，每篇附带 breakdown 分数明细
- **Trend Engine**：基于 OpenRouter Embedding 的语义聚类 + cosine 相似度分组 + 突发检测（7日基线对比），自动发现 breaking/rising 趋势话题，追踪趋势演化生命周期
- **Multi-Agent 日报生成**：Fetch Agent → Analyst Agent → Trend Agent → Editor Agent → Critic Agent 五步流水线，自动输出 Markdown 格式行业日报（趋势信号 → 关键事件 → 信号热点 → 行业解读 → 明日观察）
- **FastAPI 后端**：`/daily-report` `/daily-report/markdown` `/trends` `/items` `/stats` `/health` 六个端点，支持分页查询、评分过滤、标签筛选

### 已有能力

- **13 个内置信源**：官方 AI RSS（OpenAI / Anthropic / Google DeepMind / HuggingFace / NVIDIA 等 30+ feed）、AI Breakfast、Follow Builders、9 个聚合站（TechURLs / Buzzing / Info Flow / BestBlogs / TopHub / Zeli / AI HubToday / AIbase / NewsNow）、GitHub Trending + Vercel 模板市场
- **三阶段智能去重引擎**：URL 归一化 → 深度标题归一化（剥离前后缀/渠道标识） → Bigram Jaccard 相似度模糊合并
- **多源热度加权**：多源报道热度累加增益，前端"多源聚合"勋章 + 毛玻璃徽章直达其余信源
- **工业级灾备**：原子写入 + 翻译缓存断崖式暴跌校验自动备份
- **Payload 极致压缩**：首屏 JSON 体积压缩超 40%
- **双视图模式**：AI 强信号 / 全量情报一键切换
- **智能分类**：AI / 科技 / 数码 / 硬件 / 开源热榜，关键词标签自动标注
- **中英双语**：英文标题自动翻译中文，双行显示
- **AI 摘要**：接入 OpenRouter 自动生成 30 字 TL;DR（可选）
- **消息推送**：支持企业微信 / 钉钉 / 飞书 / Markdown webhook（可选）
- **WaytoAGI 时间线**：今日 / 近 7 日切换
- **源健康面板**：失败源、零数据源、自动替换/跳过一目了然
- **自定义 OPML**：导入 RSS 订阅，扩展信源覆盖
- **纯静态部署**：GitHub Pages + GitHub Actions 自动更新，零服务器成本

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | 单文件 SPA（index.html + app.js），预编译 Tailwind CSS，深色 glassmorphism 主题 |
| 后端管线 | Python 3.11+，feedparser + BeautifulSoup + requests |
| AI 引擎 | OpenRouter API（TL;DR 摘要 + Embedding 向量 + Multi-Agent 报告） |
| API 层 | FastAPI + uvicorn（可选，v3 新增） |
| 趋势引擎 | scikit-learn cosine_similarity + 纯 Python fallback（v3 新增） |
| 配置 | YAML 驱动（sources.yaml / score_weights.yaml / model_config.yaml） |
| 部署 | GitHub Pages 静态托管，GitHub Actions 每小时自动更新 |
| SEO | Open Graph / Twitter Card / sitemap / robots.txt / PWA manifest |

## 快速开始

### 普通用户

直接打开线上页面，无需安装任何东西。

### Fork 自己的版本

1. Fork 本仓库
2. 在 GitHub Settings → Pages 中开启 Pages
3. 保留 `.github/workflows/update-news.yml`，它会每小时自动更新数据
4. 可选：将你的 OPML 内容 base64 编码后存入 GitHub Secret `FOLLOW_OPML_B64`

### 本地运行（v3）

```bash
git clone https://github.com/821920046/ai-news-radar-enhanced.git
cd ai-news-radar-enhanced
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# CLI 模式（兼容 v2）
python scripts/update_news.py --output-dir data --window-hours 24

# v3 Pipeline API
python core/pipeline/main_pipeline.py --window-hours 24

# 开启 Trend Engine（需要 OPENROUTER_KEYS）
python core/pipeline/main_pipeline.py --window-hours 24 --trend-engine

# FastAPI 模式
uvicorn api.app:app --host 0.0.0.0 --port 8000 --reload
# 访问 http://localhost:8000/docs 查看 Swagger 文档

# 静态前端
python -m http.server 8080
```

打开 `http://localhost:8080`

## v3 项目结构

```
├── core/                      # v3 核心引擎
│   ├── fetch/                 # 13 信源抓取器
│   │   ├── rss_fetcher.py     #   官方 AI RSS（30+ feed）
│   │   ├── github_fetcher.py  #   GitHub Trending + Vercel 生态
│   │   ├── aggregators.py     #   聚合站（TechURLs/Buzzing/InfoFlow/TopHub/...）
│   │   ├── aihub.py           #   AI HubToday / AIbase
│   │   ├── newsletters.py     #   AI Breakfast / BestBlogs
│   │   ├── builders.py        #   Follow Builders
│   │   ├── opml.py            #   自定义 OPML/RSS 导入
│   │   └── waytoagi.py        #   WaytoAGI 飞书 Wiki
│   ├── normalize/             # 归一化 + 翻译
│   │   ├── normalizer.py      #   AI 关键词过滤、主题分类、标签标注
│   │   └── translator.py      #   EN→ZH Google 翻译 + 缓存保护
│   ├── dedup/                 # 三阶段智能去重
│   │   └── deduplicator.py    #   URL 精确 → 标题极净 → Bigram Jaccard 模糊
│   ├── signal_score/          # Signal Score 2.0（v3 新增）
│   │   ├── scorer.py          #   5 维加权评分引擎（S/A/B/C）
│   │   ├── features.py        #   特征提取（velocity/novelty/community）
│   │   ├── feedback.py        #   用户反馈收集
│   │   └── outcome_tracker.py #   评分效果追踪
│   ├── trend_engine/          # Trend Engine（v3 新增）
│   │   ├── clustering.py      #   OpenRouter Embedding + 语义聚类
│   │   ├── burst_detection.py #   7 日基线突发检测
│   │   ├── trend_detector.py  #   编排器
│   │   └── evolution.py       #   趋势生命周期追踪
│   ├── agents/                # Multi-Agent System（v3 新增）
│   │   ├── fetch_agent.py     #   抓取调度
│   │   ├── analyst_agent.py   #   TL;DR + 深度分析
│   │   ├── trend_agent.py     #   趋势解读
│   │   ├── editor_agent.py    #   日报生成
│   │   └── critic_agent.py    #   质量控制
│   ├── pipeline/              # 主流水线
│   │   └── main_pipeline.py   #   Pipeline 类（7 阶段编排）
│   ├── models.py              #   数据模型
│   ├── utils.py               #   工具函数
│   ├── recommend.py           #   推荐理由生成
│   ├── notifier.py            #   Webhook 推送
│   ├── archive.py             #   存档管理
│   ├── output.py              #   Payload 输出
│   └── logging_config.py      #   日志配置
├── api/                       # FastAPI 层（v3 新增）
│   ├── app.py                 #   FastAPI 应用 + 6 路由
│   └── routes.py              #   模块化路由 hook
├── config/                    # 配置文件（v3 新增）
│   ├── sources.yaml           #   数据源配置
│   ├── score_weights.yaml     #   评分权重
│   └── model_config.yaml      #   模型配置
├── scripts/                   # 向后兼容 shim 层
│   ├── update_news.py         #   CLI 入口 → core.pipeline
│   └── fetchers/              #   → core.fetch
├── configs/
│   └── topic_rules.json       #   分类关键词规则
├── data/                      #   自动生成的 JSON 数据
├── frontend/
│   ├── index.html
│   ├── assets/app.js
│   └── assets/tailwind.min.css
├── feeds/
│   └── follow.example.opml    #   OPML 模板
├── requirements.txt           #   依赖（新增 fastapi/scikit-learn/numpy/pyyaml）
├── manifest.json
├── robots.txt
├── sitemap.xml
└── .github/workflows/
    └── update-news.yml        #   每小时自动更新 CI
```

## API 端点（v3 新增）

| 端点 | 说明 |
|------|------|
| `GET /health` | 健康检查 |
| `GET /daily-report` | 最新 24h 日报（JSON） |
| `GET /daily-report/markdown` | 最新 24h 日报（Markdown） |
| `GET /trends` | 当前趋势话题 |
| `GET /items?limit=20&min_score=50&level=A` | 分页查询，支持评分/等级/标签过滤 |
| `GET /stats` | 数据统计摘要（信源数/条目数/失败源） |

## 自定义订阅源

支持导入你自己的 OPML/RSS 订阅。

### 本地

```bash
cp feeds/follow.example.opml feeds/follow.opml
# 编辑 feeds/follow.opml
python scripts/update_news.py --output-dir data --window-hours 24 --rss-opml feeds/follow.opml
```

### GitHub Actions 自动化

1. 编辑好 `feeds/follow.opml`
2. 终端执行 `base64 < feeds/follow.opml`（macOS）或 `certutil -encode follow.opml follow.b64`（Windows），复制输出
3. GitHub 仓库 → Settings → Secrets and variables → Actions → New secret
4. Name: `FOLLOW_OPML_B64`，Value: 粘贴内容，保存
5. CI 每小时自动解码并抓取

## 可选配置

| 环境变量 | 说明 |
|---------|------|
| `OPENROUTER_KEYS` | OpenRouter API Key（逗号分隔），启用 AI TL;DR + Trend Engine |
| `OPENROUTER_MODEL` | TL;DR 模型（默认 `google/gemma-4-31b-it:free`） |
| `AI_TLDR_ENABLED` | 开启 AI TL;DR（`0`/`1`） |
| `AI_TLDR_TOP_N` | 每轮摘要条数上限（默认 10） |
| `TREND_ENGINE_ENABLED` | 开启趋势引擎（`0`/`1`，v3 新增，消耗 API 额度） |
| `WEBHOOK_URL` | Webhook 推送地址 |
| `WEBHOOK_TYPE` | 推送类型：`markdown` / `wechat` / `dingtalk` / `feishu` |
| `WEBHOOK_MODE` | 推送模式：`digest` / `breaking` |
| `FOLLOW_OPML_B64` | GitHub Secret，base64 编码的 OPML 内容 |

## 数据输出

| 文件 | 说明 |
|------|------|
| `data/latest-24h.json` | AI 精选 24h 数据（含 signal_score/signal_level/signal_breakdown） |
| `data/latest-24h-all.json` | 全量 24h 数据（懒加载） |
| `data/archive.json` | 3 天滚动存档 |
| `data/source-status.json` | 信源健康状态 |
| `data/waytoagi-7d.json` | WaytoAGI 近 7 日更新 |

## 给 AI Agent 使用

项目 Skill 定义在 `skills/ai-news-radar/SKILL.md`。

```bash
# 交接提示词
请读取这个仓库，并使用 skills/ai-news-radar/SKILL.md。
先看 README.md、docs/GPT_HANDOFF.md、docs/SOURCE_COVERAGE.md、docs/V2_PRODUCT_BRIEF.md。
请验证这个项目是否已经达到可发布状态，并指出需要修复的具体问题。
```

## 升级路线

| 版本 | 能力 |
|------|------|
| v1 | RSS 列表、简单去重 |
| v2 | 三阶段去重、双语标题、AI 摘要、多源热度、Payload 优化 |
| v3 | Signal Score 2.0、Trend Engine、Multi-Agent 日报、FastAPI、YAML 配置 |

## 许可

MIT

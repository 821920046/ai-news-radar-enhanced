# AI Signal Board

24 小时 AI/科技/数码/硬件全向情报雷达。纯文字、高密度、标题驱动。

实时聚合 12+ 高质量信源，自动分类去重，中英双语标题，AI 精选评分。支持自定义 OPML/RSS 订阅导入。

## 在线访问

```
https://821920046.github.io/ai-news-radar-enhanced/
```

## 特性

- **12 个内置信源**：官方 AI RSS（OpenAI / Anthropic / Google DeepMind / HuggingFace / NVIDIA 等 25+ feed）、AI Breakfast、Follow Builders、9 个聚合站（TechURLs / Buzzing / Info Flow / BestBlogs / TopHub / Zeli / AI HubToday / AIbase / NewsNow）
- **双视图模式**：AI 强信号 / 全量情报，一键切换
- **智能分类**：AI / 科技 / 数码 / 硬件，关键词标签自动标注
- **中英双语**：英文标题自动翻译中文，双行显示
- **热度排序**：综合来源权重、标签密度、AI 摘要等维度的 60-99 评分
- **AI 摘要**：接入 OpenRouter 自动生成 30 字 TL;DR（可选）
- **消息推送**：支持企业微信 / 钉钉 / 飞书 / Markdown webhook（可选）
- **WaytoAGI 时间线**：今日 / 近 7 日切换
- **源健康面板**：失败源、零数据源、自动替换/跳过一目了然
- **自定义 OPML**：导入你的 RSS 订阅，扩展信源覆盖
- **纯静态部署**：GitHub Pages + GitHub Actions 自动更新，零服务器成本

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | 单文件 SPA（index.html + app.js），预编译 Tailwind CSS，深色 glassmorphism 主题 |
| 后端 | Python 3.11 数据管线（feedparser + BeautifulSoup + requests） |
| 部署 | GitHub Pages 静态托管，GitHub Actions 每小时自动更新 |
| 性能 | WebP 多尺寸 logo（1.5MB → 14KB），预编译 CSS（300KB CDN → 28KB），紧凑 JSON |
| SEO | Open Graph / Twitter Card / sitemap / robots.txt / PWA manifest |

## 快速开始

### 普通用户

直接打开线上页面，无需安装任何东西。

### Fork 自己的版本

1. Fork 本仓库
2. 在 GitHub Settings → Pages 中开启 Pages
3. 保留 `.github/workflows/update-news.yml`，它会每小时自动更新数据
4. 可选：将你的 OPML 内容 base64 编码后存入 GitHub Secret `FOLLOW_OPML_B64`

### 本地运行

```bash
git clone https://github.com/821920046/ai-news-radar-enhanced.git
cd ai-news-radar-enhanced
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python scripts/update_news.py --output-dir data --window-hours 24
python -m http.server 8080
```

打开 `http://localhost:8080`

## 自定义 OPML 订阅

导入你自己的 RSS/OPML 订阅，扩展信源覆盖：

```bash
# 1. 复制模板
cp feeds/follow.example.opml feeds/follow.opml

# 2. 编辑 feeds/follow.opml，替换为你的订阅源
# （此文件已在 .gitignore 中，不会被提交）

# 3. 运行时指定 OPML
python scripts/update_news.py --output-dir data --window-hours 24 --rss-opml feeds/follow.opml
```

**GitHub Actions 方式**：将 OPML 文件内容 base64 编码，存入仓库 Secret `FOLLOW_OPML_B64`，CI 会自动解码使用。

## 可选配置

以下均为可选，核心抓取流程不需要任何 API Key：

| 环境变量 | 说明 |
|---------|------|
| `OPENROUTER_KEYS` | OpenRouter API Key（逗号分隔），启用 AI TL;DR 摘要 |
| `AI_TLDR_TOP_N` | 每轮摘要条数上限 |
| `WEBHOOK_URL` | Webhook 推送地址 |
| `WEBHOOK_TYPE` | 推送类型：`markdown` / `wechat` / `dingtalk` / `feishu` |
| `WEBHOOK_MODE` | 推送模式：`digest`（定时摘要）/ `breaking`（热度突破） |
| `FOLLOW_OPML_B64` | GitHub Secret，base64 编码的 OPML 内容 |

## 数据输出

| 文件 | 说明 |
|------|------|
| `data/latest-24h.json` | AI 精选 24 小时数据（紧凑格式） |
| `data/latest-24h-all.json` | 全量 24 小时数据（懒加载） |
| `data/archive.json` | 3 天滚动存档 |
| `data/source-status.json` | 信源健康状态 |
| `data/waytoagi-7d.json` | WaytoAGI 近 7 日更新 |

## 项目结构

```
├── index.html              # 前端单页应用
├── assets/
│   ├── app.js              # 前端逻辑
│   ├── tailwind.min.css    # 预编译 Tailwind CSS
│   ├── logo.webp           # 多尺寸 WebP logo
│   └── logo-180.webp       # apple-touch-icon / og:image
├── scripts/
│   ├── update_news.py      # 数据管线入口
│   ├── fetchers/           # 12 个信源抓取器
│   ├── utils.py            # 工具函数
│   ├── topic_filter.py     # 分类与过滤
│   ├── translate.py        # 中英翻译
│   ├── recommend.py        # 评分与推荐
│   ├── ai_processor.py     # AI 摘要（可选）
│   └── notifier.py         # Webhook 推送（可选）
├── configs/
│   └── topic_rules.json    # 分类关键词规则
├── feeds/
│   └── follow.example.opml # OPML 模板
├── data/                   # 自动生成的 JSON 数据
├── manifest.json           # PWA 配置
├── robots.txt
├── sitemap.xml
└── .github/workflows/
    └── update-news.yml     # 自动更新 CI
```

## 覆盖策略

采用双层设计：

- **默认层**：给普通 AI 爱好者的 AI 强信号流，信源稳定、公开、无需登录
- **进阶层**：给维护者的 OPML 自定义、源健康面板、GitHub Actions 部署配置

不将 X API、邮箱、cookies 等需要登录态的源作为公共默认源。详细策略见 `docs/SOURCE_COVERAGE.md`。

## 给 AI Agent 使用

项目 Skill 定义在 `skills/ai-news-radar/SKILL.md`。

交接提示词：

```
请读取这个仓库，并使用 skills/ai-news-radar/SKILL.md。
先看 README.md、docs/GPT_HANDOFF.md、docs/SOURCE_COVERAGE.md、docs/V2_PRODUCT_BRIEF.md。
请验证这个项目是否已经达到可发布状态，并指出还需要修复的具体问题。
```

## 许可

MIT

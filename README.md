<p align="center">
  <img src="./assets/logo.webp" alt="AI Signal Board Logo" width="120" />
</p>

# AI Signal Board

<p align="center">
  24 小时 AI/科技/数码/硬件全向情报雷达。纯文字、高密度、标题驱动。
</p>

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

## 自定义订阅源

支持导入你自己的 OPML/RSS 订阅，扩展信源覆盖。

### 方式一：本地运行时导入

```bash
# 1. 从模板复制
cp feeds/follow.example.opml feeds/follow.opml

# 2. 编辑 feeds/follow.opml，加入你的订阅源
# （此文件已在 .gitignore 中，不会被提交到仓库）

# 3. 运行时指定 OPML 文件
python scripts/update_news.py --output-dir data --window-hours 24 --rss-opml feeds/follow.opml
```

### 方式二：GitHub Actions 自动化（推荐）

只需配置一次，之后每小时自动抓取你的订阅源：

1. 编辑好 `feeds/follow.opml` 文件（本地）
2. 终端执行 `base64 < feeds/follow.opml`（macOS）或 `certutil -encode follow.opml follow.b64`（Windows），复制输出内容
3. 打开 GitHub 仓库 → **Settings** → **Secrets and variables** → **Actions** → **New secret**
4. Name 填 `FOLLOW_OPML_B64`，Value 粘贴 base64 内容，保存
5. 完成。CI 每小时运行时会自动解码并抓取你的订阅源

### OPML 文件格式

```xml
<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head><title>我的订阅</title></head>
  <body>
    <!-- 可以有多层分组 -->
    <outline text="AI 官方" title="AI 官方">
      <outline type="rss" text="OpenAI Blog" title="OpenAI Blog"
        xmlUrl="https://openai.com/blog/rss.xml"
        htmlUrl="https://openai.com/blog" />
      <outline type="rss" text="Anthropic" title="Anthropic"
        xmlUrl="https://www.anthropic.com/rss.xml"
        htmlUrl="https://www.anthropic.com" />
    </outline>
    <outline text="科技媒体" title="科技媒体">
      <outline type="rss" text="36氪快讯" title="36氪快讯"
        xmlUrl="https://rsshub.app/36kr/newsflashes"
        htmlUrl="https://36kr.com/" />
    </outline>
  </body>
</opml>
```

### 从哪里获取 OPML？

大多数 RSS 阅读器支持导出 OPML：

| 阅读器 | 导出路径 |
|--------|---------|
| Feedly | Settings → Import/Export → OPML Export |
| Inoreader | Settings → Import/Export → Export OPML |
| NetNewsWire | File → Export Subscriptions |
| Miniflux | Settings → Import → 导出 OPML |
| Follow (follow.is) | Settings → Import & Export |

导出后直接使用，或只保留你需要的源。

### 支持的订阅源类型

- 标准 RSS / Atom feed（绝大多数博客和新闻站）
- RSSHub 桥接源（`rsshub.app` 路径，覆盖 B站、知乎、微博等）
- 项目会自动跳过不支持的源类型（Telegram、Bilibili 直链等），并尝试将已知的 RSSHub 坏链替换为官方 feed

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

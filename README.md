# AI Signal Board

中文 | [English](#english)

高质量 AI/科技新闻聚合项目。普通用户直接打开网页看 24 小时 AI 信号；维护者可以 fork 后接入自己的 OPML/RSS；Codex / Claude Code 可以用项目内 Skill 继续添加信息源和优化产品。

说明：本仓库已适配公开发布，**不会包含作者私有 RSS 订阅文件**。

## 中文

### 0. 你是哪类用户？

| 你想做什么 | 直接入口 |
| --- | --- |
| 我只是想看 AI 新闻 | 打开 `https://learnprompt.github.io/ai-news-radar/` |
| 我想 fork 一个自己的版本 | 看下面的「1 分钟上手」和「GitHub 自动更新」 |
| 我想给 Codex / Claude Code 用 | 看 `skills/ai-news-radar/SKILL.md` 和 `docs/GPT_HANDOFF.md` |

### 1. 在线入口

- 线上页面：
  - `https://learnprompt.github.io/ai-news-radar/`
- 说明：
  - 日常查看请打开这个页面，不要直接打开 `data/latest-24h.json`
  - GitHub Actions 会持续更新 `data/*.json`，GitHub Pages 会展示最新页面

### 2. 1 分钟上手

普通用户不需要安装任何东西，直接打开线上页面即可。

想 fork 自己的版本：

1. Fork 本仓库。
2. 在 GitHub Pages 里开启 Pages。
3. 保留 `.github/workflows/update-news.yml`，它会定时更新 `data/*.json`。
4. 可选：把你的 OPML base64 内容放进 GitHub Secret `FOLLOW_OPML_B64`。

想本地运行：

```bash
git clone https://github.com/LearnPrompt/ai-news-radar.git
cd ai-news-radar
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/update_news.py --output-dir data --window-hours 24
python -m http.server 8080
```

打开：`http://localhost:8080`

### 3. 这个项目每天更新需要一直开 Codex 吗？

不需要。你只要执行一个命令，或者直接用 GitHub Actions 定时运行即可。

- 本地命令（一次）：
  - `python scripts/update_news.py --output-dir data --window-hours 24 --rss-opml feeds/follow.opml`
- 自动化（推荐）：
  - `.github/workflows/update-news.yml` 已配置定时任务，默认每 30 分钟自动更新并提交数据。

### 4. 主要能力

- 官方 AI 节点直连（OpenAI News / OpenAI Codex Changelog / OpenAI Skills / Anthropic / Google DeepMind / Google AI / Hugging Face / GitHub AI）
- 高信号日报补充（AI Breakfast）
- AI builders 中心化 feed 补充（Follow Builders：X builders / Anthropic Engineering / Claude Blog / AI podcasts）
- 9 个聚合源补充覆盖（TechURLs / Buzzing / Info Flow / BestBlogs / TopHub / Zeli / AI HubToday / AIbase / NewsNow）
- OPML RSS 批量接入（私有文件 `feeds/follow.opml`，仓库提供模板 `feeds/follow.example.opml`）
- 24h 双视图：`AI强相关` / `全量`
- 全量模式去重开关
- AI 模式默认去重
- 覆盖雷达：源健康、今日覆盖池、AI精选、官方/日报源池、Builders/X源池、私人扩展
- 站点与分区聚合展示
- 中英双语标题显示
- WaytoAGI：`最近更新日` / `近7日` 切换
- RSS 失败源自动处理：
  - 能替换官方源则自动替换
  - 无官方 RSS 的源自动跳过，避免浪费抓取时间
- 告警数据输出：
  - `failed_feeds` / `zero_item_feeds` / `skipped_feeds` / `replaced_feeds`

### 5. 覆盖范围

这个项目采用双层设计：

- 默认层：给普通 AI 爱好者直接使用的 `AI强相关` 信号流。
- 进阶层：给维护者使用的 OPML、自定义源、源健康状态与 GitHub Actions 部署配置。

默认层覆盖：

- 官方 RSS / Atom / changelog
- 高信号 newsletter 公开归档
- GitHub 公开生成的 feed，例如 Follow Builders
- 多个公开聚合站

进阶层覆盖：

- 个人 OPML / RSS
- GitHub Secret `FOLLOW_OPML_B64`
- 未来可选的 X API、邮箱、WeChat 等 secret-backed adapter

不把 X API、邮箱、cookies、WeChat 登录态作为公共默认源。它们对普通用户不稳定，也容易引入隐私和维护成本。
详细覆盖策略见 `docs/SOURCE_COVERAGE.md`。

源可靠性说明：

- OpenAI News RSS 不覆盖所有开发者侧小更新；因此默认层额外抓取 OpenAI Codex Changelog，并过滤接入 OpenAI Skills 仓库里和 Codex/宠物相关的更新。
- X/Twitter 没有官方 RSS。`feeds/social-x.example.opml` 提供 Karpathy 的 RSSHub 候选示例，适合按需复制进自己的 OPML；公共默认层不强依赖它，避免第三方 X 桥不稳定时拖垮站点。
- Follow Builders 使用中心化 GitHub Actions + 官方 X API 抓取公开 X 内容，本项目只读取它公开发布的 JSON feed；这比公共 RSSHub 稳定，但依赖第三方中心 feed。
- AI Breakfast 的 Beehiiv 原始 `/feed` 在命令行和 GitHub Actions 场景可能被 Cloudflare 拦截；默认层通过 Jina Reader 读取公开归档页，只取公开标题和链接。

### 6. 给 Codex / Claude Code 使用

项目 Skill 在：

- `skills/ai-news-radar/SKILL.md`

让新的 Agent 接手时，推荐给它这句话：

```text
请读取这个仓库，并使用 skills/ai-news-radar/SKILL.md。
先看 README.md、docs/GPT_HANDOFF.md、docs/SOURCE_COVERAGE.md、docs/V2_PRODUCT_BRIEF.md。
请验证这个项目是否已经达到可发布状态，并指出还需要修复的具体问题。
```

完整交接说明见 `docs/GPT_HANDOFF.md`。

### 7. 数据输出

- `data/latest-24h.json`
- `data/archive.json`
- `data/source-status.json`
- `data/waytoagi-7d.json`
- `data/title-zh-cache.json`

### 8. 本地自定义 OPML

```bash
cd ai-news-radar
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp feeds/follow.example.opml feeds/follow.opml
# 把你自己的 OPML 内容替换到 feeds/follow.opml（不要提交到仓库）
python scripts/update_news.py --output-dir data --window-hours 24 --rss-opml feeds/follow.opml
python -m http.server 8080
```

打开：`http://localhost:8080`

### 9. Secrets / API 配置说明（重要）

默认情况下，本项目 **不需要任何 API Key** 才能运行核心抓取流程。  
你目前没有提供 API 密钥，仓库中也不会写入任何密钥信息。

推荐仅在运行环境中配置（不要提交到仓库）：

- 代理（可选）：
  - `HTTP_PROXY`
  - `HTTPS_PROXY`
- 如果你未来接入私有 API/私有 RSS：
  - 把密钥放到 GitHub Secrets（Actions）或本地环境变量
  - 不要写入代码、README、日志、`.env` 示例中的真实值
- 私有 RSS OPML（推荐）：
  - GitHub Actions Secret：`FOLLOW_OPML_B64`
  - 生成方式（macOS/Linux）：
    - `base64 < feeds/follow.opml | pbcopy`（macOS）
    - 然后把内容粘贴到 GitHub 仓库的 Secrets

### 10. GitHub 自动更新

工作流：`.github/workflows/update-news.yml`

- 定时：每 30 分钟
- 任务：执行抓取命令并提交 `data/*`
- RSS OPML：若设置了 `FOLLOW_OPML_B64`，工作流会自动解码为 `feeds/follow.opml`
- 推送权限：使用 `GITHUB_TOKEN`（workflow 内）

---

## English

### 0. Who Is This For?

| Goal | Start here |
| --- | --- |
| Read AI news | Open `https://learnprompt.github.io/ai-news-radar/` |
| Fork your own version | Use the 1-minute setup below |
| Hand it to Codex / Claude Code | Read `skills/ai-news-radar/SKILL.md` and `docs/GPT_HANDOFF.md` |

### 1. Live Site

- Live page:
  - `https://learnprompt.github.io/ai-news-radar/`
- Note:
  - Use this page for daily reading instead of opening `data/latest-24h.json` directly
  - GitHub Actions keeps `data/*.json` updated and GitHub Pages serves the latest UI

Production-grade AI/tech news aggregator with a static web UI, 24h updates, WaytoAGI timeline, and OPML RSS ingestion.

This repo is safe for public release and does **not** include the maintainer's private RSS subscription file.

### 2. 1-Minute Setup

Readers do not need to install anything. Open the live page.

To fork your own version:

1. Fork this repository.
2. Enable GitHub Pages.
3. Keep `.github/workflows/update-news.yml`; it updates `data/*.json`.
4. Optional: add your OPML as the GitHub Secret `FOLLOW_OPML_B64`.

To run locally:

```bash
git clone https://github.com/LearnPrompt/ai-news-radar.git
cd ai-news-radar
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/update_news.py --output-dir data --window-hours 24
python -m http.server 8080
```

Open: `http://localhost:8080`

### 3. Do I need Codex running all day?

No.  
You only need to run one command, or let GitHub Actions run it on schedule.

- One-shot local command:
  - `python scripts/update_news.py --output-dir data --window-hours 24 --rss-opml feeds/follow.opml`
- Scheduled automation:
  - `.github/workflows/update-news.yml` runs every 30 minutes and commits updated data.

### 4. Core features

- Multi-source web aggregation
- First-class official AI update sources (OpenAI News / OpenAI Codex Changelog / OpenAI Skills / Anthropic / Google DeepMind / Google AI / Hugging Face / GitHub AI)
- High-signal newsletter coverage (AI Breakfast)
- AI builders central feed coverage (Follow Builders: X builders / Anthropic Engineering / Claude Blog / AI podcasts)
- 9 aggregator sources for breadth (TechURLs / Buzzing / Info Flow / BestBlogs / TopHub / Zeli / AI HubToday / AIbase / NewsNow)
- OPML RSS ingestion (private `feeds/follow.opml`; template provided as `feeds/follow.example.opml`)
- 24h two-mode UI (`AI-focused` / `All`)
- Dedup toggle in All mode, dedup-by-default in AI mode
- Coverage radar for source health, signal density, official/newsletter, builders/X, aggregator breadth, and private extension paths
- Site + section grouping
- Bilingual title rendering
- WaytoAGI toggle (`Today` / `Last 7 Days`)
- RSS resilience:
  - Auto-replace failed feeds with official sources when available
  - Auto-skip unsupported source types (to save crawl time)
- Alert-friendly status output (`failed_feeds`, `zero_item_feeds`, `skipped_feeds`, `replaced_feeds`)

### 5. Custom sources and agent workflow

This project uses a two-layer design:

- Default layer: a curated `AI-focused` signal feed for ordinary AI enthusiasts.
- Advanced layer: OPML, custom sources, source health, and GitHub Actions deployment settings for maintainers.

For custom sources, prefer `feeds/follow.opml` locally or GitHub secret `FOLLOW_OPML_B64` in Actions. Do not commit private subscription files.
See `docs/SOURCE_COVERAGE.md` for source strategy. The Codex / Claude Code project skill lives at `skills/ai-news-radar/SKILL.md`.

Source reliability notes:

- OpenAI News RSS does not cover every developer-side minor update, so the default layer also reads the OpenAI Codex Changelog and filtered OpenAI Skills updates related to Codex/pets.
- X/Twitter has no official RSS. `feeds/social-x.example.opml` includes a Karpathy RSSHub candidate for personal OPML use, but the public default layer does not depend on it.
- Follow Builders uses centralized GitHub Actions plus the official X API to fetch public X content. This project reads its public JSON feeds, which is more stable than public RSSHub but depends on a third-party central feed.
- AI Breakfast's Beehiiv `/feed` can be blocked by Cloudflare from CLI or GitHub Actions, so the default layer reads the public archive through Jina Reader and extracts public titles and links.

For agent handoff, use:

```text
Please inspect this repository and use skills/ai-news-radar/SKILL.md.
Start with README.md, docs/GPT_HANDOFF.md, docs/SOURCE_COVERAGE.md, and docs/V2_PRODUCT_BRIEF.md.
Verify whether this project is ready to publish and list concrete remaining issues.
```

### 6. Output files

- `data/latest-24h.json`
- `data/archive.json`
- `data/source-status.json`
- `data/waytoagi-7d.json`
- `data/title-zh-cache.json`

### 7. Quick start

```bash
cd ai-news-radar
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp feeds/follow.example.opml feeds/follow.opml
# Replace with your own OPML subscriptions (do not commit this file)
python scripts/update_news.py --output-dir data --window-hours 24 --rss-opml feeds/follow.opml
python -m http.server 8080
```

Open: `http://localhost:8080`

### 8. Secrets / API notes

By default, this project needs **no API keys** for the core pipeline.  
No secrets are stored in this repo.

If you later add private APIs/feeds:

- Use environment variables or GitHub Secrets
- Never commit real tokens/keys
- For private RSS OPML in GitHub Actions, store `base64` content in secret `FOLLOW_OPML_B64`

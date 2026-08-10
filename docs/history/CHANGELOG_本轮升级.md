# 本轮升级说明（时间/手机分类/每日定时推送）

## 1. 时间统一为北京时间（UTC+8）
**根因**：`assets/app.js` 里的时间格式化（`fmtTime`/`fmtClock`/`fmtDate`/`fmtDateGroup`/`dateKey`）依赖访客浏览器本地时区，导致不同地区/服务器渲染出的时间与日期分组不一致。

**修复**：全部强制使用 `Asia/Shanghai`。
- `fmtTime` / `fmtClock`：`Intl.DateTimeFormat` 增加 `timeZone: "Asia/Shanghai"`。
- `fmtDate`：改为直接解析 `YYYY-MM-DD` 字符串键，避免二次时区偏移。
- 新增 `APP_TZ` + `tzParts()` 辅助函数，`fmtDateGroup`（“7月8日·周三”）与 `dateKey`（日期分组键）统一按北京时间计算年/月/日/星期。
- `relTime`/`relTimeShort` 基于时间戳差值，无需改动。
- 服务端 `scripts/prerender.py` 不渲染可见的时:分文本（仅 ISO 时间戳），因此前端修复即可覆盖首屏。

## 2. 顶部导航新增“手机”类目
主要面向全球手机市场最新新闻。
- `core/normalize/normalizer.py`：新增 `TOPIC_PHONE_KEYWORDS`（手机/iPhone/折叠屏/骁龙/天玑/麒麟/各大厂商机型等），`classify_item` 分类优先级改为 **开源热榜 > 手机 > 电脑硬件 > 数码 > AI > 科技**。
- `configs/topic_rules.json`：同步新增 `TOPIC_PHONE_KEYWORDS`，可自定义覆盖。
- `assets/app.js`：`CATEGORY_META`/计数/导航数组新增“手机”（天蓝色主题），位于“数码”与“电脑硬件”之间。
- `index.html`：新增“手机”卡片左边框颜色 `#0ea5e9`。

## 3. 企微推送：由“热点即时推送”改为“每天早上7点定时推送6条”
- `core/notifier.py`：默认模式改为 `daily`。
  - 每天 **北京时间 07:00** 固定推送。
  - 共 **6 条**：**AI（2）/ 数码科技（数码+科技，2）/ 手机电脑（手机+电脑硬件，2）**，每组按热度取 Top2、标题去重。
  - 格式：每条一行 **标题（≤10字，带链接）** + 一行 **内容（≤50字）**。
  - 时间门控 `_beijing_now()` = UTC+8；未命中 7 点则跳过；`WEBHOOK_FORCE=1` 可手动强制发送测试。
  - 旧的 `breaking`（热点即时）与 `digest` 模式仍保留，可通过 `WEBHOOK_MODE` 切换。
- `.github/workflows/update-news.yml`：`WEBHOOK_MODE` 固定为 `daily`，并新增 `WEBHOOK_DAILY_HOUR: "7"`。工作流原本每小时运行（`cron: 0 * * * *`），其中 UTC 23:00 一次即对应北京时间 07:00，届时触发推送。

### 可调环境变量
| 变量 | 默认 | 说明 |
|------|------|------|
| `WEBHOOK_MODE` | `daily` | `daily`/`breaking`/`digest`/`off` |
| `WEBHOOK_DAILY_HOUR` | `7` | 每日推送的北京时间小时 |
| `WEBHOOK_DAILY_PER_GROUP` | `2` | 每个类目条数 |
| `WEBHOOK_FORCE` | 空 | 设为 `1` 忽略时间门控立即推送（调试用） |

## 验证
- `node --check assets/app.js` 通过；`topic_rules.json` / workflow YAML 合法。
- `python -m py_compile` 通过；55 个单元测试全部 OK。
- 行为测试：手机分类路由、每组 Top2 选取、标题≤10/内容≤50 截断、07:00 门控均符合预期。

## 补充：前端时间“晚于站点更新时间/偏差”修复

**病因（第一性原理）：** 部分新闻显示的不是“文章真实发布时间”，而是代理时间：
- NewsNow 聚合源对很多子源不提供单条 pubDate，旧逻辑用整块 updatedTime（NewsNow 刷新时间）冒充发布时间；
- Readhub/36氪/TopHub 等热榜无发布时间，回退用 first_seen_at（抓取时间）；
- 部分 RSS 源（Vercel/Pinecone/HuggingFace 等）只给日期或存在时钟偏差，时间可能“超前”，导致新闻时间晚于站点构建时间。

**修复（三层）：**
1. 流水线 `core/pipeline/main_pipeline.py`：时间健全化——发布时间不得晚于本次构建时间(now+10分钟容差)，超前则置空回退。
2. 抓取器 `core/fetch/aggregators.py`：NewsNow 不再用整块 updatedTime 冒充发布时间，无真实单条时间时留空。
3. 前端 `assets/app.js`：无真实发布时间或发布时间晚于构建时间的条目，时间前加 “≈”、来源行标“采集”，并鼠标悬停提示，不再冒充真实发布时间。

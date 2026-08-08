# 升级说明（2026-07 · 第一性原则安全/健壮性强化）

> 本轮升级在既有 `OPTIMIZATION_REPORT.md`（上一轮已修复 6 项：热度排序、signal_score 覆盖、prerender `<script>` 截断 XSS、raw_items 遮蔽、去重 O(n²)、velocity 缓存）**之上**，针对**尚未覆盖的新问题**进行修复。所有改动均在沙箱内实测验证。

## 一、根因定位（第一性原则）

本项目会聚合 30+ 第三方源（含 `r.jina.ai`、`rsshub`、`tophub`、`newsnow` 等**不可信中转/聚合源**）。因此"条目里的 URL"必须被当作**不可信输入**。顺着这条主线审查数据流，发现三个真实缺陷：

### 缺陷 1（P0 · 存储型 XSS）：链接协议全程未校验
- **前端 `assets/app.js`**：所有把条目 URL 写入链接的地方都未做协议白名单——
  - 热点跑马灯 `href="${link}"`（且未做属性转义）；
  - 新闻卡 `titleEl.href = item.url`（DOM 属性赋值，`javascript:` 会在点击时执行）；
  - 合并信源 `a.href = src.url`；
  - WaytoAGI 的 `rootLink/historyLink/row.href`。
- **构建期 `scripts/prerender.py`**：`url = _esc(it.get("url"))`，而 `_esc = html.escape(..., quote=True)` 只转义 `& < > " '`。`javascript:alert(document.cookie)` 不含这些字符，会**原样**写进静态 HTML 的 `href`，形成**预渲染页面里的可点击 XSS**（上一轮的 P0-3 只堵了 `<script>` 截断，未堵协议）。
- **根因**：`escapeHtml`/`_esc` 只解决"文本上下文"注入，而 `href` 属于"URL 上下文"，需要**协议白名单**而非转义。

### 缺陷 2（P1 · PWA 正确性）：每小时预渲染的首屏被当静态资源"缓存优先"
- `sw.js` 把 `./`、`./index.html` 放进 SHELL，并对非 JSON 请求一律"缓存优先"。
- **后果**：CI 每小时用 `prerender.py` 重写 `index.html`（新 `<title>`、description、Open Graph、JSON-LD、首屏列表），但老访客的 Service Worker 会一直返回**旧快照**，直到缓存版本变更——SEO 与首屏"实时性"名存实亡。
- **根因**：预渲染 HTML 是**动态内容**，不是不可变的应用外壳，不能套用 cache-first。

### 缺陷 3：热点跑马灯链接文本已转义、但 `href` 属性未转义
- 属于缺陷 1 的一部分，一并修复（`escapeHtml(safeUrl(link))`）。

## 二、本轮修复

| 文件 | 改动 | 作用 |
|---|---|---|
| `assets/app.js` | 新增 `safeUrl()`；7 处链接赋值全部经其过滤 | 仅放行 `http/https/mailto` 与相对链接，阻断 `javascript:`/`data:`/`vbscript:` 等可执行协议；跑马灯 href 额外做属性转义 |
| `scripts/prerender.py` | 新增 `_safe_url()`；条目 `href` 改用它 | 先做协议白名单、再 `_esc` 转义，堵住静态页 XSS |
| `sw.js` | HTML/导航请求改"网络优先→回退缓存"；缓存版本 `v1→v2` | 保证每小时预渲染的首屏与 SEO 内容最新；离线仍可回退上次快照；版本号提升让存量用户的旧缓存立即失效 |

`safeUrl()` 关键实现：先剥离浏览器会忽略的 `\t\n\r`（防 `java\tscript:` 绕过）并 `trim`，再用 `^([a-z][a-z0-9+.-]*):` 提取协议，非白名单协议一律返回 `#`；无协议的相对/锚点链接原样放行。`_safe_url()` 逻辑等价，且返回前再做 `_esc`。

## 三、沙箱内实测（均已通过）

1. **单元向量**：`safeUrl` 15/15、`_safe_url` 13/13 通过（覆盖大小写混淆 `JaVaScRiPt:`、前导空格、内嵌 `\t/\n`、`data:`/`vbscript:`、相对/锚点、`&`/`"` 转义）。
2. **端到端预渲染**：用真实 `data/latest-24h.json` 跑 `prerender.py`，注入 30 条、幂等标记正常、输出 `javascript:` href 计数为 0。
3. **恶意数据回归**：构造 `url=javascript:alert(document.cookie)`、`title=<script>...`、`tldr=</script><script>` 的条目 → 预渲染输出中该链接被中和为 `href="#"`，无原始 `<script>`。
4. **前端冒烟**：`chromium --headless` 加载打补丁后的页面（真实数据），渲染出 55 张新闻卡片，退出码 0，无我方代码的 `Uncaught/TypeError/ReferenceError`。
5. **语法/编译**：`node --check` 通过（`app.js`、`sw.js`），`py_compile` 通过（`prerender.py`）。

## 四、验证边界（诚实说明）

沙箱**无外网**、且缺少 `pytest/feedparser/fastapi` 依赖，因此**未能**运行完整 `pytest` 套件与实时抓取管线；上述验证聚焦于可离线复现的部分（前端、预渲染、SW 逻辑、静态检查）。建议合并前在有网环境执行仓库自带的 `pytest tests/` 做一次全量回归。

## 五、后续建议（未在本轮实施，供你决策）

- **CSP 收紧**：`_headers` 现为 `script-src 'self' 'unsafe-inline'`。页面大量使用内联 `<style>` 但内联 `<script>` 较少，可评估将脚本改为 nonce/hash，去掉脚本的 `unsafe-inline`。
- **GitHub Pages 无视 `_headers`**：安全响应头仅在 Cloudflare Pages 生效。若主站在 GitHub Pages，建议用 `<meta http-equiv>` 补一份 CSP，或迁移到 Cloudflare Pages。
- **服务端 SSRF**：OPML/RSS 抓取会请求用户提供的任意 URL（CI 侧），可加内网地址段黑名单。

---

## 六、企业微信每日 07:00 推送重构（`core/notifier.py`）

### 根因
- **标题不知所云**：`build_daily_digest_message` 里 `headline = _clip(title, 10, ellipsis=False)` —— **把标题硬截成 10 个字符且不加省略号**，于是"Geosql：地理空间数据的 Claude/Codex 技能"变成"Geosql：地理空"、"Show HN: 免费美人鱼图示编辑器"变成"Show HN: 免"。
- **内容也没有**：正文取 `tldr → description → title_zh → title`。而真实数据中 **`tldr` 恒为空**（AI TL;DR 默认关闭）、`description` 多数为空，于是正文一路回退到 `title`，**只是把标题又抄了一遍**。

### 修复
- 标题改用**完整中文标题**（`title_zh` 优先），仅在超过 34 字时才带省略号截断。
- 正文改为 `_pick_summary()`：优先真实摘要（`tldr`/`description`），**且与标题不同才展示**；没有真实摘要时不再复读标题，而由 `_digest_meta_line()` 给出"**来源 · #标签 · 信号分 · 🔥热榜**"的上下文信息行。
- 来源清洗：去掉板块后缀与括号注释（"Hacker News · 24h最热"→"Hacker News"，"IT之家 (ITHome)"→"IT之家"）。
- 版式重排：`# 📡 每日科技情报 · M月D日 周X` 表头 + 分类 emoji 分节（🤖/💻/📱）+ 编号链接 + 灰色 `<font color="comment">` 元信息行；并加 3800 字节兜底截断（企微单条上限约 4096 字节）。

### 验证（真实数据模拟 07:00 推送）
- `py_compile` 通过；原有 `tests/test_notifier.py` 4 项用例 `unittest` 全过。
- 用 `data/latest-24h.json` 生成实际推送文本：标题完整、有 `description` 的条目显示英文/中文摘要、其余显示来源/标签/信号上下文，总长 1693 字节。

### 已知遗留（上游内容质量，未在本轮改）
- 选材由 `select_daily_digest` 按热度/信号排序，个别条目存在**上游分类/打标签错误**（如一条 AP 社会新闻被归入"数码科技"并打上"模型发布"标签）。这属于 `core/normalize`/分类器的问题，需在管线侧修正，本轮推送层仅如实呈现。

---

## 七、推送分类重构 + 分类器第一性原理修复

### 7.1 推送类目重构（`core/notifier.py`）
- 「数码科技」→「**3C数码**」，涵盖 `数码 / 手机 / 电脑硬件`（合并原「手机电脑」组）。
- 「科技」类目取消，并入「**AI**」大类目（`AI + 科技`）。
- 由 3 组改为 2 组，每组 `per_group` 2→3，保持“每日 6 条”；副标题类目名改为根据分组**动态生成**；emoji 更新为 🤖 / 📱。

### 7.2 关键词匹配的第一性原理 bug（`core/normalize/normalizer.py`）
- **根因**：`contains_any_keyword` 用最朴素的子串匹配 `k in h`，导致短英文关键词命中单词内部：`ide` 命中 **"v`ide`o"**、`ai` 命中 **"s`ai`d"**、`release` 命中任意英文。一条“国民警卫队枚击”新闻因此被错打上「模型发布」(release)、「编码工具」(ide∈video)。
- **修复**：纯 ASCII 关键词改为**词边界正则**匹配（`(?<![a-z0-9])kw(?![a-z0-9])`），中文等非 ASCII 关键词仍用子串匹配；并收紧「模型发布」词库（去掉通用动词 `发布/release/launch/announce`，改为模型名 + `大模型/模型发布/开源模型/new model` 等）。
- **实测（data/latest-24h.json 全量 895 条）**：「模型发布」标签 319→138、「编码工具」136→67（清除大量假阳性）；同时 "OpenAI releases GPT-5.5 model" 仍正确识别为模型发布。`ai∈said`、`ide∈video` 均不再误匹。影响全管线（前端标签/导航也一并受益）。

### 7.3 推送选材相关性闸门（`_is_digest_worthy`）
- **根因**：`is_ai_related_record` 对 `zeli`（Hacker News 24h 热榜）直接放行，使枚击/政治等硬新闻绕过 AI 过滤进入推送。
- **修复**：选材时增加相关性闸门——先排除娱乐/体育/彩票等 `NOISE_KEYWORDS`，再要求命中具体品类 category / AI 信号 / 真科技内容词。**相关性只看内容（标题+摘要）不看来源名**（否则 TOPIC_TECH_KEYWORDS 里的 "hacker news" 会让所有 HN 条目误通过）。
- **实测**：那条枚击新闻 `_is_digest_worthy=False`，已不可能进入推送。

### 7.4 验证
- `py_compile` 通过；`tests/test_notifier.py` + `tests/test_topic_filter.py` 共 **25 项 `unittest` 全过**。
- 真实数据模拟 07:00 推送：2 组各 3 条共 6 条，标题完整、切题、含摘要/上下文，总长 1906 字节。

### 7.5 已知残留
- 「多模态」标签词库含通用词 `video/image/audio`，仍可能在含“video”的非 AI 新闻上误标（但推送已由相关性闸门兼顧）；如需进一步，可要求该类关键词与 AI 上下文共现。
- `开源热榜` 类目目前不属于任何推送分组（与原逻辑一致），如需可并入 AI 组。

# 免费层面优化补丁（安全 / 性能 / SEO / 趋势引擎）

本补丁全部为**零成本、免费层**改动，不引入任何付费依赖。

## P0 安全
- **新增 `_headers`**：Cloudflare Pages 安全/缓存响应头（CSP、X-Frame-Options、X-Content-Type-Options、Referrer-Policy、HSTS、Permissions-Policy）。
  - ⚠️ **你是 GitHub Pages + Cloudflare 代理，`_headers` 不生效**（GitHub Pages 不支持自定义响应头）。请按 `README_Cloudflare安全头.md` 用 **Cloudflare Transform Rules** 设置（免费）。`_headers` 仅在你以后迁到 Cloudflare Pages 时才生效。
- **`api/app.py`**：新增零依赖中间件 —— 按客户端 IP 的内存滑动窗口**限流**（`RATE_LIMIT_PER_MIN`，默认 120/min），并为所有响应注入**安全头**与 `Cache-Control: public, max-age=300`（`PUBLIC_CACHE_SECONDS` 可调）。
- **移除 `.claude/settings.local.json`** 并在 `.gitignore` 忽略 `.claude/`（此前泄露内部自动化命令白名单）。

## P1 性能
- **删除 `assets/log.png`（1.5MB）**；logo 兜底改为 `logo-32.webp`（含 `this.onerror=null` 防循环）。
- **移除 Google Fonts 外链**，改用系统字体栈（更快、更私密、零外部依赖；Tailwind 已自带回退栈）。
- **新增 `sw.js` Service Worker**：数据 JSON 走网络优先（保实时），静态资源缓存优先（秒开 + 离线可看上次快照）；`index.html` 注册 SW。

## P2 SEO
- **`scripts/prerender.py`**：生成 `sitemap.xml`（robots.txt 已声明），并补充 `<link rel=canonical>`、`og:url`，默认 `og:image` / `twitter:image` 指向 `assets/social-preview.png`。可用 `--base-url` 覆盖站点域名。
- 工作流自动提交 `sitemap.xml`。

## P3 趋势引擎转为免费
- **`core/trend_engine/clustering.py`**：新增**纯 Python TF-IDF + 余弦贪心聚类**（`clustering_method="tfidf"` 为默认），不再调用付费 embedding。可选 `embedding`（付费）/ `tag` 模式。默认阈值 `tfidf_similarity_threshold=0.18`（经真实数据校准）。
- **`core/pipeline/main_pipeline.py`**：产出 `data/trends.json`（供 `/trends` 与前端消费）。
- **工作流默认开启 `--trend-engine`**（现已 100% 免费），并提交 `data/trends.json`。

## 可调环境变量
- `RATE_LIMIT_PER_MIN`(120)、`PUBLIC_CACHE_SECONDS`(300)、`ALLOW_ORIGINS`
- `tfidf_similarity_threshold`(0.18)、`tfidf_max_docs`(400)、`clustering_method`(tfidf)

## UI 细节打磨（保持深色青绿主题）
- `index.html` 新增一小段样式：键盘聚焦可见环（无障碍）、主题化细滚动条、平滑滚动、尊重“减少动效”系统偏好（暂停雷达扫描/跑马灯/闪烁）、图片占位背景。不改变原有配色与布局。

## 部署提醒
- `_headers` 仅在 **Cloudflare Pages** 自动生效；你现在是 GitHub Pages，请看 `README_Cloudflare安全头.md`。
- 首次启用 Service Worker 后，旧访客需刷新一次以注册。

# Cloudflare Workers KV「list 每日限额已超出」根因与免费额度方案

> 邮件原文关键词:**Workers KV `list` 操作**已用 1000 次(免费档每日上限)。
> 注意:是 **list**(枚举 key),**不是 write(写入)**。方向不要搞反。

## 一、第一性原理结论

**本项目通篇没有任何 Cloudflare Workers KV 代码。** 已全仓库核查:

- 无 `wrangler.toml`、无 `functions/`、无 `_worker.js`、无 KV 绑定、无 `.list()` / `.put()` 调用。
- `sw.js` 是**浏览器 Service Worker**(浏览器 Cache API),与 Cloudflare Workers KV 无关,不计入 KV 额度。
- 前端无定时轮询(`hot-ticker.js` 的 `refreshMs` 默认 0,且未被 `index.html` 引用)。

因此 KV `list` 报警**不可能**由本项目「写入/读取量」直接产生。它来自:

1. **你的部署方式**:很可能把静态站部署到了 **Cloudflare Workers(Workers Sites / 用 KV 存静态资源)** 或带 KV 的 Pages Function 上——这类方案会把「未命中缓存的请求」转成 KV 操作;或
2. 账号里**另一个 Worker** 在请求路径上调用了 `KV.list()`。

## 二、项目侧的「帮凶」——已修复

前端 4 个 JSON 请求原本带 `?t=${Date.now()}` 时间戳,使每个 URL 唯一,**导致 Cloudflare 边缘缓存永远无法命中**(连既有的「JSON 缓存 5 分钟」Cache Rule 都被作废),每个访客每次打开都直穿源站。数据每小时才更新一次,这个缓存穿透纯属浪费,还会把每次请求放大成一次源站/KV 操作。

**修复**:移除 `assets/app.js` 中 4 处 `?t=${Date.now()}`,改为干净 URL。配合边缘缓存与 Service Worker 网络优先,数据最多滞后几分钟(对每小时更新的数据完全无感),而源站/KV 命中量呈数量级下降。

## 三、根治方案(二选一,都免费)

### 方案 A(推荐):静态托管,彻底不碰 KV
把站点用**纯静态**方式托管,静态资源服务**不消耗 Workers KV 额度**:
- **GitHub Pages**(本仓库原生方式,`prerender.py` 的 base_url 即 GitHub Pages),或
- **Cloudflare Pages** 的「直接上传/连接 Git」静态托管(注意:不要加使用 KV 的 Function)。

迁移后 KV 用量归零,报警消失。安全响应头按 `README_Cloudflare安全头.md` 用 Transform Rules 设置。

### 方案 B:保留 KV 支撑的 Worker,但别在请求路径上 list
若你确实想用 Worker 提供服务:
- **禁止**在每次请求里调用 `env.NAMESPACE.list()`;list 只应在后台低频任务里用,或改为直接 `KV.get(具体key)`。
- 给 JSON/HTML 设置 **Cache Rules(边缘 TTL 5 分钟)** + 响应 `Cache-Control`,让绝大多数请求命中边缘缓存、不回源、不触发 KV。

## 四、如何定位真正的 KV 来源(必做)

Cloudflare 控制台:
1. **Workers & Pages → KV**:看有哪些 Namespace、哪个在涨。点进去看 **Metrics** 的 `list` 曲线。
2. **Workers & Pages → 你的 Worker → Metrics / Logs**:确认哪个 Worker 在调用 `list`。
3. 若那个 Worker 就是用来「服务这个站点」的,按方案 A 切静态即可下线它。

## 五、验证

- 部署后用无痕窗口多次刷新页面,`curl -sI https://你的域名/data/latest-24h.json | grep -i cf-cache-status` 应逐渐出现 `HIT`(说明边缘缓存生效、不再回源)。
- 观察 Cloudflare KV 的 `list` 曲线是否回落到 0 / 免费额度内。

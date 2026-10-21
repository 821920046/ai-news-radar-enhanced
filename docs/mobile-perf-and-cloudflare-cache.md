# 移动端打开慢：诊断与 Cloudflare 缓存配置

> 2026-10-21：用户反馈手机打开 `news.my-tv.eu.cc` 每次都要等很久
> （截图停在骨架屏「加载中... / 0 条」）。本文记录诊断过程、已修的代码问题，
> 和还需要在 Cloudflare 面板点一下的配置。

## 一、诊断结论（实测，非猜测）

用 `curl -w` 对线上取样：

| 观测 | 结果 |
|---|---|
| 经 CF 拉 index.html | TTFB 在 **0.4s ↔ 43s** 之间波动；某次 TLS 握手 5.7s |
| 不带压缩拉 `latest-24h.json` | **100 秒没传完**（有效吞吐最差 ~30KB/s） |
| 对照组（百度） | 0.11s |
| 直连 GitHub Pages 源站 | TTFB 3.4s |

`cf-cache-status` 响应头（决定性证据）：

| 路径 | 状态 | 含义 |
|---|---|---|
| `/`（HTML） | **DYNAMIC** | 每次都回源 GitHub Pages |
| `/data/latest-24h.json` | **DYNAMIC** | 每次都回源（600KB gzip） |
| `/assets/tailwind.min.css` | HIT | 命中边缘缓存 |
| `/assets/app.js` | EXPIRED | 缓存过期，本次回源 |

**两层根因**：

1. **Cloudflare 没有边缘缓存 HTML 和 JSON。** `.json` 不在 Cloudflare 默认
   缓存扩展名清单里；HTML 因源站 `Cache-Control: max-age=600` 且默认规则
   不缓存 HTML 而同样是 DYNAMIC。于是每次手机打开：CF → 跨海/拥塞路径 →
   GitHub Pages 源站，遇上网络波动就是十几到几十秒。
   注意：仓库根目录的 `_headers` 是 **Cloudflare Pages** 的格式，本站跑在
   GitHub Pages + CF 反向代理上，`_headers` 完全不生效；线上的
   `Cache-Control: max-age=600` 是 GitHub Pages 的默认值。
2. **slim 快照一半是重复数据**（已在代码层修复，见下）：`items` 与 `items_ai`
   是同一个列表，被 `json.dumps` 序列化两遍；外加两个没人读的字段。

## 二、已修的代码问题（commit `df4c38c`）

`core/output.py build_latest_payloads`：

- 拆掉重复的 `items` 键（所有消费方 app.js / build_feeds / prerender /
  validate_data / send_daily_digest / api 都优先读 `items_ai`）；
- 剥离 `signal_breakdown`（仅 llms.txt 文档表格提及，无 item 级读取方）与
  `compact_score`（recommend.py 写入后零消费方）。

`assets/app.js`：热榜改读 `payload.items_ai`。

**效果**（对当前真实快照实测）：

| 文件 | 修复前 | 修复后 |
|---|---|---|
| latest-24h.json 原始 | 3.01 MB | 1.34 MB（−55%） |
| latest-24h.json gzip | 571 KB | 284 KB（−50%） |
| latest-24h-all.json | 6.05 MB | 5.31 MB |

下一次 hourly 流水线自动产出瘦身后的快照，无需手动操作。

## 三、还需要你做：Cloudflare Cache Rule（最大杠杆）

代码瘦身把「每次传 571KB」降到「每次传 284KB」，但只要 `cf-cache-status`
还是 DYNAMIC，弱网下依然可能等很久。去 Cloudflare 面板配两条缓存规则，
让 HTML/JSON 直接从边缘节点返回：

1. 登录 Cloudflare → 选择域名 → 左侧 **Caching（缓存）→ Cache Rules** → Create rule。
2. **规则 1（数据）**：
   - Field: `URI Path`，Operator: `starts with`，Value: `/data/`
   - 选 **Cache eligible**（Eligible for cache）
   - Edge TTL: **Use TTL from origin** 或 **Override: 30 分钟**
     （数据每小时更新一轮，30 分钟边缘 TTL 最多旧半小时）
3. **规则 2（HTML）**：
   - Field: `URI Path`，Operator: `equals`，Value: `/`
   - 同样 **Cache eligible**，Edge TTL: **Override: 10 分钟**
     （首页是预渲染的，10 分钟足够让首访走边缘）
4. 保存顺序：把这两条放在最上面（Cache Rules 从上到下匹配）。

> `/assets/*` 已经能命中（.js/.css 在默认缓存清单里），不用管。
> 如果面板叫 Page Rules（旧版），就配 `*news.my-tv.eu.cc/data/*`
> → Cache Level: Cache Everything, Edge TTL: 30 minutes。

**验证方法**（配完后跑，应看到 `HIT` 而不是 `DYNAMIC`）：

```bash
curl -sI https://news.my-tv.eu.cc/data/latest-24h.json | grep -iE "cf-cache|cache-control"
# 期望: cf-cache-status: HIT
```

连续请求两次：第一次 MISS，第二次起 HIT，TTFB 应从秒级降到百毫秒级。

## 四、后续可选优化（未做，按需再说）

1. **首屏分包**：`latest-24h.json` 只放前 ~60 条（约 60KB），其余进
   `latest-24h-more.json`，首次滚动到底再拉。前端已是 50 条/批渲染，
   改造点在 `init()` 与 `loadAllModeData` 的懒加载逻辑。
   注意：筛选器/站点统计目前从前端全量 items_ai 计算，分包需要把这些
   聚合值随 slim payload 一起下发。
2. **字段继续瘦**：`description`（1062/1310 条有值）可只在详情展开时按需拉取，
   但需要改前端交互，暂不动。

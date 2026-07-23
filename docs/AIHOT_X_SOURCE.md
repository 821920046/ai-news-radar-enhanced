# 接入 AIHOT（aihot.virxact.com）作为 X 订阅源

## 这次改了什么

1. **新增** `core/fetch/aihot_virxact.py`
   - 消费 AIHOT 的公开只读 API `GET /api/public/items`（带 cursor 翻页）。
   - AIHOT 在上游已用官方 X API 抓好、清洗好推文，直接消费它的 API 比公共 RSSHub 稳定得多，且能拿到干净的 X 内容。
   - 字段做了多别名容错（title/title_zh、url/link/permalink、source/sourceName、publishedAt/date… ），返回结构兼容 items/data/results 与 nextCursor/cursor。

2. **启用** `core/fetch/__init__.py`
   - 在 `collect_all` 的 tasks 里注册了 `("aihot", "AI HOT", fetch_aihot_virxact)`。
   - （注意：原来那个 `fetch_aihot` 抓的是另一个站 `aihot.today`，且从未注册；本次不动它。）

3. **去重优先级** `core/dedup/deduplicator.py`
   - 在 `_pick_best_item` 的 `site_priority` 里加入 `"aihot": 88`（介于 opmlrss=90 与 buzzing=80 之间），
     这样当 AIHOT 的条目与其它源命中同一新闻时，跨源合并会按合理优先级挑代表条目，不会产生重复。

4. **新增** `scripts/probe_aihot.py`
   - 探针脚本：在你自己的环境跑一下，打印 API 真实 JSON 结构，用来核对字段名。

## 上线前务必做一步：核对字段

我这边沙盒没有外网，无法实测 API。请在本地或 CI 跑：

```bash
python scripts/probe_aihot.py
```

把打印出来的「第一条条目字段」与 `core/fetch/aihot_virxact.py` 里 `_first(...)` 的别名对一下；
如果官方字段名不同，补进对应 `_first(item, ...)` 即可（改一行就行）。

## 可调环境变量（都可选）

| 变量 | 默认 | 说明 |
|---|---|---|
| `AIHOT_API_BASE` | `https://aihot.virxact.com/api/public` | API 根地址 |
| `AIHOT_MODE` | `all` | `selected` 只要精选 / `all` 全量 |
| `AIHOT_TAKE` | `100` | 每页条数 1–100 |
| `AIHOT_MAX_PAGES` | `5` | 最多翻页数（防死循环）|
| `AIHOT_X_ONLY` | 关 | 设 `1` 时**只保留来源是 x.com/twitter 的条目**（纯 X 订阅源）|
| `AIHOT_CATEGORY` | 空 | 限定分类 `ai-models\|ai-products\|industry\|paper\|tip` |

> 想让 AIHOT 只当「X 订阅源」用：设 `AIHOT_X_ONLY=1`。
> 想让它补充全站广度：留默认（全量入库，交给你现有去重兜底）。

## 备用方案

若你更想走 RSS 路线，仓库根目录另附 `ai-news-radar-extra-sources.opml`，
内含 ~17 个 X 账号（RSSHub 路由）+ 高信噪比英文/中文源，复制进 `feeds/follow.opml` 即可。

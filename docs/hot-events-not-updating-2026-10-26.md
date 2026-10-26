# 「热点 2 天没在前端更新」—— 第一性原则诊断与修复（2026-10-26）

用户报告：**热点已经 2 天没在前端页面更新了。**

结论先说：**数据一直在更新，页面上的「热点」从来就不是热点。**
生产、持久化、部署、缓存四层全部健康；断层在消费侧。

---

## 一、先证明数据链路是健康的

按数据流从生产到消费逐层核对，每层都有可复核的证据。

### 1. 生产侧：Stage 7 每轮都跑，`hot_events` 非空

`gh run list --workflow=update-news.yml`：最近 15 轮全部 `success`。
最新一轮 `40302369070`（2026-10-26T09:48Z）日志：

```
[Pipeline] Stage 7/7: Trend Engine + Report generation...
[Pipeline] Report generated (7651 chars, validated: good, hot_events: 49).
[Pipeline] Complete: {'success': True, 'trends_detected': True, 'hot_events': 49, ...}
```

**`hot_events: 49`，不是 0。** 趋势引擎没有停。

### 2. 持久化侧：快照确实被提交并推送

同一轮 `Commit and push changes` 步骤：

```
[CHANGED] data/latest-24h.json
[main 9b3a5b1] chore: update ai news snapshot + prerender
 9 files changed, 3557 insertions(+), 134 deletions(-)
   3e0d19c..9b3a5b1  main -> main
```

`origin/main:data/latest-24h.json` 的 `generated_at = 2026-10-26T09:48:40Z` —— 新鲜。

### 3. 部署侧：线上服务的就是这份产物

`deploy-pages.yml` 的 verify 作业（run `40303011161`）：

```
expected generated_at: 2026-10-26T09:48:40.815837Z
attempt 1: HTTP 200  curl_exit=0  served=2026-10-26T09:48:40.815837Z
live site matches the published artifact
```

### 4. 缓存侧：Cloudflare 边缘缓存每轮都清

```
CF_API_TOKEN: ***
Cloudflare edge cache purged
```

### 5. 一个必须排除的假线索

本地 `main` 落后 `origin/main` 十几个提交（本地停在 5 天前），
所以**直接读工作区里的 `data/latest-24h.json` 会看到 5 天前的 `generated_at`**。
这不是线上状态。排查时必须以 `git show origin/main:...` 为准。

---

## 二、断层在消费侧：三处

### 缺陷 1（根因）—— 页面上的「当前热点」不是热点

`assets/app.js` 的 `renderStats()` 原文：

```js
const hottestNews = [...allItems]
  .sort((a, b) => {
    const ha = (a.hotness != null ? a.hotness : (a.stars != null ? a.stars : a.signal_score)) || 0;
    ...
  })
  .slice(0, 15);
```

即「把 `items_ai` 按分数排序取前 15 条」。这是**高分单篇**，不是热点。
热点的语义是「多个独立信源在同一时间段里共同报道的同一件事」——
趋势引擎 `heat-v1` 算的正是这个，结果写在 `payload.hot_events` 里。

**但 `hot_events` 从来没有被前端消费过。** `git log -S "hot_events" -- assets/app.js`
返回空 —— 一次都没有过。

### 缺陷 2（为什么「看起来一直没变」）—— 排序字段名全错

生产端写的是 `hotness_score`（`core/dedup/deduplicator.py`、`core/agents/*`）
和 `meta.stars_today`（GitHub 热榜）。前端读的是 `a.hotness` / `a.stars`。

**两个字段在 payload 里都不存在。** 后果不是报错，而是：

- 两个分支永远不成立 → 排序**恒等退化**成 `signal_score`；
- GitHub 热榜的真实 star 数（`hotness_score` 最高 632）被静默忽略。

实测 `items_ai` 668 条的构成：

```
official_ai: 197   ← 其中 188 条是 arXiv cs.AI
aihot:       110
buzzing:      99
iris:         78
...
```

这 188 篇 arXiv 论文：

- `published_at` **全部**是 `2026-10-26T04:00:00Z`（同一批合成时间戳）；
- `signal_score` 只有两个值：`60.2`（141 条）和 `66.5`（42 条）。

于是 top-15 里 **13 席**是 66.5 的 arXiv 论文，同分 + 同时间 →
顺序完全由数组位置决定。标题又高度同质化（中文 AI 论文标题），
**用户每次打开看到的都是同一类东西** → 「热点没更新」。

### 缺陷 3 —— 真正的 `hot_events` 缺可渲染字段

```python
hot_events = [
    {
        "topic": ..., "heat": ..., "heat_trend": ...,
        "heat_participants": ..., "heat_badges": ...,
        "heat_sources": ..., "size": ...,
    }
    ...
]
```

**没有 `url`，没有标题。** 前端拿到的是一串不可点击的短语，
即使想渲染也无从下手。这才是「数据在算但没人用」的真正原因。

### 缺陷 4 —— `data/trends.json` 冻结两个月

- 最后一次提交：`2026-08-24 03:58`（两个月前）；
- **仓库里没有任何代码写它**（`grep -rn "trends.json" --include="*.py"` 只有 `api/app.py` 在读）；
- 却仍在 `deploy-pages.yml` 的发布白名单里，对外提供 `/data/trends.json`；
- `/api/trends` 的逻辑是「有 `trends` 就直接返回」—— 于是这个接口
  两个月来一直返回 2026-08-24 的数据。

**「文件存在」被当成了「数据新鲜」。**

### 缺陷 5（放大因素）—— cron 名义每小时，实际 3–6 小时

`cron: "0 * * * *"`，但实测最近 40 次调度间隔：

```
+5.0h +6.4h +4.9h +3.7h +3.1h +5.9h +6.3h +4.8h +3.8h +3.1h
+5.8h +6.1h +5.0h +3.7h +5.1h +5.0h +3.9h +3.6h +4.9h +5.1h
+4.0h +3.3h +3.3h +3.7h +6.2h        （平均 4.0 小时）
```

GitHub 对 `schedule` 触发有节流，这不是配置错误，但意味着
**数据新鲜度的实际上限是 6 小时，不是 1 小时**。

---

## 三、修复

### 1. `hot_events` 补上可点击的代表条目

`core/trend_engine/trend_detector.py` 新增 `_pick_representative()`，
从簇的 `items` 里挑一条，随事件一起输出 `url` / `title` / `title_zh` /
`site_id` / `site_name` / `published_at`。

挑选规则（都围绕「读者最可能想点开哪一条」）：

1. **必须有 url** —— 没链接的条目渲染不成热点行；
2. **优先有标题的** —— 只有 url 没标题时前端只能退回显示簇话题
   （「开源」「模型发布」），而话题可能和那条链接的实际内容对不上；
3. `signal_score` 高的优先；
4. 同分取事件时间更新的；
5. 时间不可解析的排同分末尾 —— 与 `heat.py`「不可证明时间不计入热度」同向。

**纯加法**：过滤条件 `heat_participants >= 2` 一字未动。已用生产数据验证
「旧口径计数 == 新 `hot_events` 数」（17 == 17，带/不带 `expected_site_ids` 都成立）。

### 2. 前端「当前热点」改为优先渲染 `hot_events`

`assets/app.js`：`hotEventRows()` → 事件级；为空时 `articleRows()` 回退到高分单篇。

- 副标题如实反映口径：`多信源热度 · 随时间消退` / `高分单篇 · 暂无多源事件`；
- `heat_trend === "unknown"` **不给任何箭头** —— 不把「没抓到」画成「变冷了」。

### 3. 修正字段名 + 回退路径加来源多样性约束

- `a.hotness` → `it.hotness_score`；
- `a.stars` → `meta.stars_today` / `meta.total_stars`；
- 同一信源最多占 `PER_SITE_CAP = 3` 席。

回退路径的效果（同一份生产数据）：

```
修复前（纯 signal_score，无来源上限）      修复后
1. EvoSkill v2 (arXiv)                    1. debpalash/VoiceStudio (GitHub, 632⭐)
2. AgriciDaniel/claude-seo                2. TauricResearch/TradingAgents
3. MedCaseAgent (arXiv)                   3. AgriciDaniel/claude-seo
4-15. 全是 arXiv 论文（同 66.5 分）        4-15. 12 个不同信源，不再被单源垄断
```

### 4. `/api/trends` 改为新鲜度优先

优先级：`latest-24h.json` 的 `hot_events` → **新鲜的** `trends.json` → tag 计数兜底。
所有分支都返回 `data_age_hours` 与 `stale` —— 只给 `generated_at` 而不给 age，
等于把换算和判断都推给消费方，而多数消费方不会做。

### 5. 停止发布 `data/trends.json`

从 `deploy-pages.yml` 的发布白名单、`paths` 触发列表、CF 清缓存清单里移除，
并从版本库删除该文件。

**判断依据：一个产物如果没有代码在写它，就不该被部署。**

### 6. llms.txt 的热度榜变成可用的

有了代表条目后，`build_feeds.py` 从「开源 — 热度 24.1」变成
「[Reddit r/opensource 封杀 AI 生成内容](https://...)（开源）— 热度 24.1，3 个独立来源，趋势 up」。
对 LLM 来说，前者几乎不携带信息。

---

## 四、验证

### 端到端（真实生产数据）

用 `origin/main` 的 668 条 `items_ai` 本地跑一遍趋势引擎：

```
新 hot_events: 17
  heat=  24.1  3源  trend=up    url=YES  [开源软件] Reddit 最大开源社区 r/opensource 的公告…
  heat=  22.5  3源  trend=down  url=YES  Kimi 就 K3.1 Pro 限流致歉：全体订阅会员补 25% 额度
  heat=  17.2  2源  trend=new   url=YES  全球大模型周调用量突破188万亿：中国AI包揽前五…
  ...
无 url 的事件: 0 / 17
```

前端渲染（Node + DOM stub 实跑 `assets/app.js` 的 `renderStats`）：

```
副标题: 多信源热度 · 随时间消退
渲染行数（去重后）: 15
   1. [3源 · 热24.1 · ▲] [开源软件] Reddit 最大开源社区 r/opensource 的公告…
   2. [3源 · 热22.5 · ▼] Kimi 就 K3.1 Pro 限流致歉：全体订阅会员补 25% 额度
   3. [2源 · 热17.2 · 新] 全球大模型周调用量突破188万亿：中国AI包揽前五…
```

### 测试

- 新增 `tests/test_hot_events_surface.py`（22 条）
- 全套件 **410 passed**

---

## 五、残余风险

1. **cron 节流**（缺陷 5）没有代码层解法。若要求「1 小时内新鲜」，
   需要外部调度器触发 `workflow_dispatch`，或接受 6 小时上限。
2. **arXiv 批量灌入**只在前端回退路径被约束（每源 3 席）。
   `items_ai` 里它仍占 28%，主列表排序仍会被它主导 —— 这是独立问题，
   需要从信源权重或 topic filter 层面处理。
3. `hot_events` 增大约 10–15KB（49 事件 × 6 字段），对 304KB 的 gzip 快照
   影响可忽略，但值得留意。

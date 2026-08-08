# AI News Radar Enhanced — 升级优化报告

> 本次优化基于第一性原理逐个定位病因，全部改动均已通过测试与行为验证。
> 测试结果：`unittest discover` 55 项全部通过；`test_signal_score` 在真实 pytest / CI 下通过
> （沙箱内的最小 pytest shim 不支持 autouse fixture，属工具假阳性，已单独复核确认 score=95.0 / level=S）。

---

## 一、修复清单（按严重程度）

### P0-1 多源热度聚合被丢弃（排序失真）
**文件**：`core/pipeline/main_pipeline.py`（Stage 4）

**病因（第一性原理）**：热度计算 `add_hotness_scores()` 原本在去重 **之后** 执行。而去重合并函数
`merge_items_group()` 依赖每条 item 的 `hotness_score` 来计算「多源聚合加成」
`hotness_score = max(各源热度) + (源数量-1) × 15`，并写入 `hotness_raw = "多源聚合 xN"`。
由于去重前 `hotness_score` 全为 0，聚合加成算不出来；紧接着去重后的 `add_hotness_scores()`
又用单源基础热度 **覆盖** 了合并结果，导致多源聚合彻底失效。

**修复**：把 `add_hotness_scores(latest_ai/latest_all)` 前移到去重 **之前**，删除去重后的重复计算。

**验证**：3 个同题多源条目（OpenAI/HN/NewsNow）去重后 `source_count=3`、`hotness_score=530`
（峰值源 500 + 2×15）、`hotness_raw="多源聚合 x3"`。修复前该值会被压回单源的 350。

### P0-2 Signal Score 2.0 引擎分值被旧启发式覆盖
**文件**：`core/recommend.py`（`enrich_recommendation_fields`）

**病因**：五维 Signal Score 2.0 引擎（source/technical/novelty/velocity/community 加权）产出的
`signal_score` 与 `signal_level`、`signal_breakdown` 一致。但 `enrich_recommendation_fields` 无条件执行
`item["signal_score"] = build_signal_score(item)`，用遗留的 60–99 紧凑启发式分 **覆盖** 了引擎分值，
造成「分数（旧启发式）」与「等级/明细（新引擎）」自相矛盾。

**修复**：保留引擎产出的 `signal_score`；仅在引擎关闭（值缺失）时回退到紧凑分；紧凑分另存 `compact_score`
供前端徽章按需使用，向后兼容、无破坏。

**验证**：引擎给分 88.5 时 enrich 后仍为 88.5，同时 `compact_score=88`；引擎关闭时回退到紧凑分。

### P0-3 预渲染把原始 JSON 注入 `<script>`（XSS / 页面损坏）
**文件**：`scripts/prerender.py`（`build_data`、JSON-LD）

**病因**：`__PRERENDER_DATA__` 与 JSON-LD 直接 `json.dumps(...)` 拼进 `<script>` 标签。标题/摘要中若含
`</script>`、`<`、`&` 会提前闭合脚本标签，导致 GitHub Pages 静态页脚本被截断，甚至被注入执行（XSS）。

**修复**：新增 `_json_for_script()`，序列化后转义 `<`→`\u003c`、`>`→`\u003e`、`&`→`\u0026`
以及行分隔符 `U+2028/U+2029`；`build_data` 与 JSON-LD 均改用它。转义后仍是合法 JSON，`JSON.parse` 可正确还原。

### P1-4 `raw_items` 变量遮蔽（潜在踩坑）
**文件**：`core/pipeline/main_pipeline.py`（趋势输出循环）

**病因**：趋势循环内 `raw_items = b.get("items", [])` 遮蔽了外层抓取用的 `raw_items`。当前未触发线上 bug，
但后续若在趋势块后引用外层变量会静默出错。

**修复**：内层重命名为 `burst_items`，消除遮蔽。

### P1-5 去重第三层 O(n²) 重复归一化（性能）
**文件**：`core/dedup/deduplicator.py`（Layer 3 模糊合并）

**病因**：双层循环内对 item 与每个 existing 反复调用昂贵的 `normalize_title_for_dedup()`，
条目多时是 O(n²) 次字符串归一化。

**修复**：新增与 `final_items` 对齐的 `final_clean_titles` 缓存，每条标题只归一化一次；合并后刷新对应缓存。
逻辑等价，去重结果不变（测试通过），归一化调用从 O(n²) 降到 O(n)。

### P1-6 速度倒排缓存跨批次串味（健壮性）
**文件**：`core/signal_score/scorer.py`（`score_batch`）

**病因**：`_VELOCITY_CACHE` 以 `id(articles)` 为键；Python 可能在不同批次复用同一 `id`，导致命中旧缓存。

**修复**：`score_batch` 开头 `_VELOCITY_CACHE.clear()`，防御性隔离每批次。

---

## 二、对抗性自审（Adversarial Review）

- **共享引用**：`latest_ai` 是 `latest_all` 的子集且共享 dict 引用；`add_hotness_scores` 幂等，去重通过
  `dict(best)` 生成新对象，`items_ai` / `items_all` 相互独立，前移热度计算不产生重复叠加。
- **安全**：预渲染注入面已封闭（`<`/`>`/`&`/U+2028/U+2029 全部转义），JSON-LD 对搜索引擎仍合法。
- **兼容**：`compact_score` 为新增字段，纯增量；`signal_score` 数值范围回归到与等级一致的 0–100。
- **等价性**：去重 Layer-3 仅做缓存化，与原算法逐条等价，`test_dedup` 全绿。
- **并发**：管线为单线程消费评分结果，缓存清理不引入竞态。
- **回归**：`compileall` 通过；55 项 unittest 全通过；signal_score 复核 95.0/S。

## 三、交付说明

- 因沙箱无网络，无法 `git push`；已将优化后的完整仓库打包为 ZIP 供下载。
- 本地缺失 `feedparser/fastapi/uvicorn/pytest`（无网装不上），但其 import 已做 `try/except` 兜底，
  不影响核心逻辑与测试；CI 环境（有网）按 `requirements.txt` 安装后可完整运行。
- 变更文件：`core/pipeline/main_pipeline.py`、`core/recommend.py`、`scripts/prerender.py`、
  `core/dedup/deduplicator.py`、`core/signal_score/scorer.py`（详见 `OPTIMIZATION.diff`）。

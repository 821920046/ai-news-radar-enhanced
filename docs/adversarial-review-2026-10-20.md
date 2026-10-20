# 对抗审查报告：弱点修复批次（2eaf195 → a4e359d）

- **审查对象**：`2eaf195`（7 项弱点修复）+ `603ce28`（YAML 重复键）+ 审查中实锤
  缺陷的修复 `a4e359d`
- **方法**：`generation-pipeline-adversarial-review` 技能 —— 写可执行探针
  （编号攻击段 + FAIL/OK，退出码 = finding 数，放 gitignored 目录、事后删除），
  攻击 A–G 七类；每个发现转为「测试名说明原始症状」的回归测试
- **测试状态**：全套件 **383 passed**（审查前 372）
- **线上验证**：CI run 39451709848（Stage 7 首秀：hot_events 29、报告 5944 字符、
  trend_history 恢复生效、4m13s）；run 39460182270（heat 行为变更复验）

## 一、结论速览

| # | 发现 | 类别 | 严重度 | 状态 |
|---|---|---|---|---|
| 1 | heat 不展开 `merged_sources`：合并事件参与者=1、被合并源误判「未跟上」 | C 跨模块不变式 | **高** | 已修 + 6 回归 |
| 2 | `trend_history` 路径不跟随 `output_dir` | C 跨模块不变式 | 中 | 已修 + 1 回归 |
| 3 | digest 把损坏快照当成「没有条目」，静默丢失当日推送 | G 空/敌意输入 | 中 | 已修 + 3 回归 |
| 4 | OPML 聚合把「预算未抓」算成「抓失败」 | F Guardrail 假阴 | 低 | 已修 + 2 回归 |

## 二、严重缺陷（修复前会在生产撒谎）

### 1. heat 引擎看不见 `merged_sources`

**攻击**：构造「3 家媒体同一标题」→ 去重合并成 1 条（痕迹只剩
`merged_sources`）→ 跑 `TrendDetector.analyze`。

**实锤**（修复前）：

- `hot_events` 为空、`heat_participants` = 1 —— 一个 5 家报道的事件从热点榜消失。
  热点榜的入选条件因此取决于**标题措辞多样性**，而不是真实跨源覆盖度。
- `heat_sources` 只剩第一家。
- `behind_sources` 报出那 4 家「未跟上」—— 它们明明报道了，只是被合并。
  连带后果：事件趋势被打成 `unknown`（「把已合并当成没抓到」，正是
  `heat.py` 文档声誓要防的那类误报）。

**根因**：`HeatEngine._aggregate` 与 `detect_behind_sources` 都只数
`item.site_id`，而 Stage 3 去重恰好跑在 Stage 7 趋势引擎**之前**——
两个模块对「一条证据」的建模不一致。

**修复**：`_aggregate` 把 `merged_sources` 展开为独立参与者（各带自己的
site_name；`owner` 实体级归并语义优先，不变）；`detect_behind_sources` 同样
计数合并来源。回归：`TestMergedSourcesCountAsParticipants`（6 条）。

### 2. 顺带验证为「打不响」的怀疑

- **去重前置后的字段口径**：`items_before_topic_filter` 仍是去重前口径（5），
  `total_items_all_mode`/`items_all` 是去重后口径（4），审计时注意到的
  slim(1242) vs all(5304) 差异确认是「slim 只装 AI 子集」的设计而非字段错位。
- **堆叠闸门**：`hot_events` 确实从 `trend_result` 流进 `latest-24h.json` →
  `build_latest_payloads` → `sanitize_public_payload` → slim payload，
  中间没有一层把它滤掉；关闭趋势引擎时键仍在（空列表，前端不 KeyError）。

## 三、中低危（已修）

### 3. `trend_history` 不跟随 `output_dir`（中）

`Pipeline.run(output_dir=<tmp>)` 时 archive / 快照 / 翻译缓存都进 tmp，
**只有趋势历史**按 CWD / `DATA_DIR` 落进仓库 `data/`。CI 没暴露，只因工作流
恰好 `--output-dir data` 且 CWD 就是仓库根。换 `output_dir` 的调用方（本地
试跑、测试）会读写彼此的状态，「冷启动」判定失真。
修复：`_get_trend_detector` 注入 `output_path/trend_history.json`
（显式配置的 `history_path` 与 `TREND_HISTORY_PATH` 仍优先）。

### 4. digest 静默丢失（中）

`load_items` 把「JSON 截断/解析失败」和「没有条目」都返回 `[]` → 打印
「没有条目，本次不推送」→ 退出码 0。工作流只有 `test -f` 存在性检查 →
当日推送**静默丢失且工作流全绿**。这个脚本的存在意义就是消灭静默丢失，
却在最需要的路径上重现了它。修复：`(items, error)` 二元返回，损坏 → 1；
真空快照仍 0。

### 5. OPML 记账混淆（低）

预算耗尽的 feed（`skipped=True`）被算进 `failed_feeds` /
`partial_failures`，「N feeds failed」把「本轮没抓」报成「抓失败」——
看告警的人会去修根本没试过的 feed。修复：failed 与 skipped 分开
（`ok + failed + skipped == 总数`），`source-status.json` 新增
`skipped_feeds`（`docs/SOURCE_COVERAGE.md` 原本就预期了这个键）。
单条记录里 `skip_reason` 一直在，属聚合口径混淆而非数据丢失。

## 四、攻击后确认打不动的部分

- **唯一发送方**：解析两个工作流后确认**恰好一个**发送路径
  （daily-digest 的 `WEBHOOK_FORCE=1`；主流水线 `WEBHOOK_MODE=none`）。
- **失败告警独立性**：`notify_ci.py` 全文不出现 `WEBHOOK_MODE`，
  `!success()` 步骤的告警不受新闻推送开关影响——注释里的承诺有代码支撑。
- **快照一致性校验无假阳性**：首轮（无 all 文件）走 WARN 分支、同刻双文件
  干净通过、混搭必红。
- **模型链四条不变式**：探测失败原样返回 / 全死无候选仍非空 /
  `exclude`（`_DEAD_MODELS`）不被补位复活 / 探测异常不抛。
- **冷启动**：无 `trend_history.json` 时 Stage 7 不崩、`behind_sources` 语义
  正确、关闭时 `hot_events` 键仍在。

## 五、残余风险（未处理，建议后续）

1. **合并事件的证据时间粒度**：展开参与者时只能复用整条 item 的事件时间
   （合并时不保留各来源的 published_at）。参与者数已正确，但「谁最先报道」
   这类时序判断在合并事件上仍粗糙。
2. **`heat_rule` 取 `clusters[0]`**：多簇时规则版本恒等于第一个簇的
   （当前实现所有簇同版本，无害；若未来分版本需改成集合）。
3. **digest 选材为空仍返回 False → 退出码 1**：`select_daily_digest` 对
   不匹配三个分组的条目会选空，此时工作流变红。属「大声失败」方向，可接受；
   若出现每日误红，应改为显式的「无素材」退出码。
4. **探针是快照不是常驻**：本次探针已删（发现已全部转为回归测试）。
   若再改 Stage 3/4/7 的顺序或 payload 结构，应重写同类探针复攻。

## 六、验证记录

| 项 | 结果 |
|---|---|
| 全套件 | 383 passed（审查前 372；heat +6、编排 +2、digest +3） |
| CI 39451709848 | success，Stage 7 首秀 hot_events 29、4m13s |
| CI 39460182270 | heat 行为变更复验（后台 watch） |
| 仓库卫生 | 探针删除后 `git status` 仅含本次修复文件；`data/trend_history.json` 本地 gitignored 且无残留 |

## 七、方法论备忘（给下一次审查）

**先区分「探针 bug」与「产品 bug」**：第一轮 5 个 FAIL 里 4 个是探针自身问题——
同一标题全被去重合并导致 Stage 7 无簇可聚、字段名写错
（`behind_sources` vs `heat_behind_sources`）、把无 env 的步骤全当成发送方、
`ProbeResult.entries` 传了 list。构造攻击输入时要让「同一事件不同措辞」：
去重只合并近似重复，趋势聚类才聚事件——这是能同时激活两条路径的唯一形态。

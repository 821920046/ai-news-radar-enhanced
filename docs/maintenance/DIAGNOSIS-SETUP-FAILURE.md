# `Failed to resolve action download info` 诊断

## 报错

```
Getting action download info
Failed to resolve action download info. Error: Service Unavailable
Retrying in 19.537 seconds
Failed to resolve action download info. Error: Service Unavailable
Retrying in 25.96 seconds
Error: Service Unavailable
Error: Failed to resolve action download info.
```

## 第一性原理分析

### 事实一：失败发生在仓库代码运行之前

失败步骤是 `Set up job`，它只做三件事：领取 runner、签发 `GITHUB_TOKEN`、解析并下载 `uses:` 引用的 action。日志停在 `Getting action download info`，因此此时仓库里的任何一行 YAML、shell 或 Python 都还没有执行。

**推论：这不可能是仓库的错，也不可能通过修改仓库代码修好。**与之前几轮的 `deployment_in_progress`、`git diff` 误判等问题属于完全不同的类别。

### 事实二：GitHub 已经重试过了，并且放弃了

日志里有两次 `Retrying in ...`（19.5s、25.9s），然后直接 `Error`。这是 runner 内部固定的有限重试，次数和间隔都不可配置。

**推论：设置阶段的失败没有任何内置的 job 级重试机制。恢复能力必须由我们显式提供。**

### 事实三：每一个 `uses:` 都是一个独立的外部依赖

解析阶段逐个处理 job 里的每个 action。N 个 action 就是 N 次外部调用，任意一次 503 就能杀掉整个 job。

**推论：未被使用的 action 不是中立的，而是纯负担。**

## 修复

### 1. 减少暂存面：删除 `actions/configure-pages@v5`

它的作用是输出 `base_path` / `origin` 供静态站点生成器使用，并在必要时启用 Pages。本项目：

- `index.html` 和 `sw.js` 全部使用 `./` 相对路径，不需要 `base_path`；
- 构建步骤从未引用它的任何输出；
- Pages 已在仓库设置中启用。

删除后 build job 的外部 action 从 3 个降为 2 个，设置阶段的失败概率直接下降约三分之一。

### 2. 提供缺失的恢复能力：`Retry Transient Failures`

新增一个 `workflow_run` 监听工作流，在目标工作流失败时自动重跑。关键在于判断条件必须精确，否则就从“自动恢复”退化成“自动掩盖真 bug”。

判断依据是确定性的，runner 会把 `Set up job` 作为一个真实步骤上报。因此每个失败 job 分为三类：

| 类别 | 判定 | 动作 |
| --- | --- | --- |
| 设置阶段 | 存在 `Set up job` 且结果为 failure | 重跑 |
| 代码阶段 | 存在非 `Set up job` 的失败步骤 | 不重跑 |
| 未上报 | 步骤列表为空（runner 提前死亡） | 回退到日志特征匹配 |

只有当所有失败 job 都属于设置阶段、且无任何代码阶段失败时才重跑。

安全护栏：

- 上限 3 次，依据 `run_attempt`。无上限会造成无限自触发循环，因为每次重跑都会再发一个 `workflow_run` 事件。
- 被人工取消的运行永不重跑。
- 读不到 job 详情时不猜，直接放弃。
- 日志特征不匹配时不重跑。

### 3. 告警降噪

`Update AI News Snapshot` 的 webhook 告警现在跳过第一次尝试。设置阶段失败会被自动重跑恢复，对它告警只会让人学会忽视告警。

## 为何不选其他方案

- **把 action 归档进仓库（vendoring）**：`upload-pages-artifact` 和 `deploy-pages` 内部依赖 Pages 后端协议，手动归档会引入更高的维护成本和更大的破坏风险，与收益不匹配。
- **在 job 内部自己重试**：不可行。失败发生在第一个步骤开始之前，我们的代码没有机会运行。
- **降低触发频率**：只能降低遭遇概率，不能消除失败，也不能恢复。

## 验证

15 组离线用例全部通过，详见 `FINAL-AUDIT.md`。

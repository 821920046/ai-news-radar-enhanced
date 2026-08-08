# 对抗审查记录（设置阶段失败专题）

## 本轮变更

| 文件 | 变更 |
| --- | --- |
| `.github/workflows/deploy-pages.yml` | 删除 `actions/configure-pages@v5`，构建 job 的外部 action 从 3 降为 2 |
| `.github/workflows/retry-transient-failures.yml` | 新增：仅针对设置阶段失败的自动重跑 |
| `scripts/retry_transient_failure.sh` | 新增：重跑判定逻辑 |
| `.github/workflows/update-news.yml` | 首次尝试失败不发 webhook 告警，让自动重跑先生效 |
| `scripts/cleanup_artifacts.sh` | 修复 `SELF_RUN_ID` 覆盖失效（见 Bug #7） |
| `tests/mock_gh.sh` | 补齐参数默认值（见 Bug #8） |
| `tests/mock_gh_retry.sh` | 新增：重跑逻辑的离线测试替身 |

## 审查中发现并修正的真问题

### Bug #7：`SELF_RUN_ID` 覆盖失效（隐蔽了一条空跑的断言）

原写法：

```sh
SELF_RUN_ID="${GITHUB_RUN_ID:-0}"
```

文档声称 `SELF_RUN_ID` 可覆盖，但这行直接忽略外部传入值。后果不是生产事故（Actions 里 `GITHUB_RUN_ID` 总是有值），而是**测试假绸**：“绝不删除本次运行自己的 artifact”这条保证在沙箱里从来没被真正验证过。修正后新增专项测试，现已 PASS。

```sh
SELF_RUN_ID="${SELF_RUN_ID:-${GITHUB_RUN_ID:-0}}"
```

### Bug #8：测试替身的未绑定变量伪造出“脚本回归”

`mock_gh.sh` 在 `set -u` 下读 `MOCK_DELETED`，未导出时报错，导致 12 组用例里 10 组变红。值得注意的是 `cleanup_artifacts.sh` 的反应是正确的：它把“stderr 非空但无 HTTP 状态码”归为失败（`? no HTTP status`）而非默认成功，并在失败率 100% 时转红。这正是第三轮加固的行为，这次意外得到了真实验证。

### Bug #9：重跑分类把“未上报步骤”误判为“代码失败”

初版把 `CODE_FAILED` 定义为“不含失败 `Set up job` 步骤的失败 job”。这是一个假二分：runner 提前死亡时步骤列表为空，既不属于设置失败也不属于代码失败，却被计入 `CODE_FAILED`，使日志特征兼容分支**永远无法进入**。修正为三分类（设置 / 代码 / 未上报），H、I、J 三组用例随即转绿。

### Bug #10：进程替换再现

分类结果原本用 `read ... < <(classify_jobs)` 读取。本沙箱的 `/dev/fd` 不可用（前两轮的 Bug #1、#2 均源于此），且失败时静默。已改为先存盘再读。

### Bug #11：救援机制与它要救的故障共享失败模式（严重）

初版 `retry-transient-failures.yml` 第一步写的是 `uses: actions/checkout@v4`。而本轮要修的故障就是“action 分发服务返回 503”。也就是说，一次 503 风波会同时打死主工作流和救援工作流，**恢复能力恰好在最需要它的时候消失**。

修正：救援工作流的 `uses:` 数量降为 **0**。GitHub 托管 runner 预装 `gh`、`jq`、`git`，因此直接走 REST API 取脚本，既避开了 action 解析路径，又保持仓库内单一份代码来源。

### Bug #12：`bash -n` 不能检出被截断的脚本

取到脚本后原本只做两道检查：非空 + `bash -n`。但**被截断的 shell 脚本在语法上完全合法**，`bash -n` 必然通过。实测：只保留前 30 行的版本顺利过关并以 exit 0 执行，而那 30 行里根本没有任何分类判定逻辑 —— 恢复能力会静默失效且日志看起来正常。`bash -n` 验证的是“能否解析”，不是“是否完整”。

修正：改取 JSON 形式而非 raw 形式。JSON 响应带 `size` 和 git blob `sha`，因此完整性变成可验证而非可假设：解码后比对字节数，并用 `git hash-object` 重算 blob sha 与 GitHub 存储的值逐字节比对。

## 新增用例（本次审查补上）

| 用例 | 场景 | 结果 |
| --- | --- | --- |
| P | 救援工作流监听到自己失败 | 结构性拒绘，不重跑 |
| Q | 监听到其他工作流失败 | 正常重跑 |
| R | 脚本下载完整 | 通过，输出字节数与 blob sha |
| S | 响应为空 | size mismatch，exit 1 |
| T | 脚本被截断（语法仍合法） | size mismatch，exit 1 |
| U | 长度相同但内容被篗改 | checksum mismatch，exit 1 |
| V | contents API 返回 503 | exit 1，不执行残缺脚本 |

U 用例特意构造为“字节数不变”，用于证明光靠 size 校验不够，sha 校验是必需的。

## 外部 action 依赖清单（每一个都是一个独立失败点）

```
deploy-pages.yml       3  checkout / upload-pages-artifact / deploy-pages
update-news.yml        2  checkout / setup-python
cleanup-artifacts.yml  1  checkout
retry-transient-failures.yml  0  救援路径必须零依赖
```

## 重跑判定逻辑：15 组离线用例

| 用例 | 场景 | 预期 | 实测 |
| --- | --- | --- | --- |
| A | 全部失败 job 死在 `Set up job` | 重跑 | 重跑 1 次，exit 0 |
| B | 仓库步骤失败 | 不重跑 | 不重跑 |
| C | 设置失败 + 代码失败混合 | 不重跑 | 不重跑 |
| D | 已是第 3 次尝试 | 不重跑 | 不重跑 |
| E | 人工取消 | 不重跑 | 不重跑 |
| F | 结果为 success | 不重跑 | 不重跑 |
| G | job 详情 API 也挂了 | 不猜，放弃 | 不重跑 |
| H | 步骤未上报 + 日志命中瞬时特征 | 重跑 | 重跑 1 次 |
| I | 步骤未上报 + 日志无特征 | 不重跑 | 不重跑 |
| J | 步骤未上报 + 取不到日志 | 不重跑 | 不重跑 |
| K | `DRY_RUN=true` | 不调 API | 不调 API |
| L | 重跑 API 报 403 | 显式报错 | `::error::` + exit 1 |
| M | `RUN_ATTEMPT=abc` | 回退为 1 | 回退为 1 |
| N | conclusion 为空 | 不重跑 | 不重跑 |
| O | `DRY_RUN=TRUE`（大写） | 仍真实重跑 | 真实重跑 |

O 是有意为之：只有字面量 `true` 才关闭动作，否则一个拼写错误就会静默关掉恢复能力。

## artifact 清理回归：12 组 + 1 专项

```
A_dry     exit=0 deleted=[]              干跑不动手
B_delete  exit=0 deleted=[107,101,102]   正常删除
C_race    exit=0 deleted=[107,101]       race_skip=1，run 1002 被重新激活后保住
D_403     exit=1 deleted=[]              权限错误立即停
E_rate    exit=0 deleted=[107,101,102]   退避后成功
F_hard    exit=1 deleted=[]              持续限流转红
G_nohttp  exit=1 deleted=[]              无 HTTP 状态码不伪装成功
H_502     exit=1 deleted=[]              5xx 转红
I_quota   exit=0 deleted=[107]           MAX_DELETES=1 生效
J_404     exit=0 deleted=[107,101,106,102] 404 容错
K_budget  exit=0 deleted=[]              时间预算让位
L_bad     exit=0 deleted=[]              全非法参数回退
专项      PASS                           artifact 109（本次 run）始终受保护
```

## 静态校验

- `bash -n`：`cleanup_artifacts.sh`、`retry_transient_failure.sh`、`mock_gh.sh`、`mock_gh_retry.sh`、`APPLY_FIX.sh` 全部通过
- `yaml.safe_load`：4 个工作流全部通过（`cleanup`、`build`+`deploy`、`retry`、`update`）
- `grep`：确认仓库内已无 `configure-pages` 引用

# 两份代码副本的差异分析（合并前的审计记录）

> 生成日期：2026-10-01
> 目的：在"以副本为基准重建"之前，把差异记录清楚，使合并且可审计、可回滚

---

## 一、两份副本的关系

工作区里有两份代码：

| | 路径 | `.git` |
|---|---|---|
| **A. 父目录（旧）** | `ai-news-radar-enhanced/` | ✅ 有（远程 `821920046/ai-news-radar-enhanced`） |
| **B. 副本（新）** | `ai-news-radar-enhanced/ai-news-radar-enhanced-main/` | ❌ 无 |

### 判定：B 是 A 的一次**重构后的新一代快照**，但从未 push 上去

证据：

1. **B 缺了 A 的兼容层**。A 的 `scripts/` 下有 ~20 个 1–6 行的 shim
   （如 `scripts/utils.py` 只有 `from core.utils import *`），B 已全部删除。
   这是"重构完成、清理残留"的典型特征。
2. **B 补齐了 A 缺失的发布链路**：`deploy-pages.yml`、`sw.js`、`config/topic_rules.json`、
   `data/trends.json`、`examples/hot-ticker/`、完整 `docs/`。
3. **`docs/STRUCTURE.md`**（B 独有）明确写了重构后的目录职责，并特别强调
   `docs/` **不放"根目录的重复副本"** —— 说明那次重构就是为了消除重复。
4. B 的 `docs/maintenance/DIAGNOSIS-SETUP-FAILURE.md` 标题正是
   *"Failed to resolve action download info"*，即 A 的 Actions 版本错误 ——
   **之前已有人诊断过同一问题，但修复没落进 A**。

### 重要：B 的代码同样是"未修复"状态

B 的 `core/models.py:15` 仍是 `SH_TZ = ZoneInfo("Asia/Shanghai")`（会崩），
`api/app.py:142` 的 `_items_of` 也仍未包含 `items_all`。
**所以 B 不是修复版，只是"文件更全的旧版"。** 本次合并需把修复移植进去。

---

## 二、文件差异清单

### 2.1 共有文件：95 个，其中 **71 个内容不同**

几乎全部文件都有差异，说明 B 不是局部修改，而是整体换代。
（`core/`、`scripts/`、`tests/`、配置文件、`index.html`、`requirements.txt` 等均在列）

### 2.2 仅 B（副本）存在 —— 56 个有效文件

这才是合并的**主要收益**：

```
.github/workflows/deploy-pages.yml            ← GitHub Pages 部署（A 缺失）
.github/workflows/cleanup-artifacts.yml       ← artifact 清理
.github/workflows/retry-transient-failures.yml
sw.js                                          ← Service Worker（A 缺失）
config/topic_rules.json                        ← 主题分类规则（A 缺失）
data/trends.json                               ← 趋势数据（A 缺失）
core/fetch/aihot_virxact.py                    ← 新抓取器
_headers, CHECKSUMS.sha256, ai-news-radar-extra-sources.opml, normalizer.py, notifier.py
docs/AIHOT_X_SOURCE.md
docs/STRUCTURE.md
docs/maintenance/*.md                          ← 7 份运维诊断报告
docs/history/*.md                              ← 历史记录
examples/hot-ticker/*                          ← 独立组件
feeds/follow.opml
scripts/cleanup_artifacts.sh, retry_transient_failure.sh, probe_aihot.py
tests/ci/*, tests/mock_gh*.sh, tests/test_dedup_enhanced.py, tests/artifacts_fixture.py
tools/ci/*.sh
README_*.md（4 份）, CHANGELOG_*.md 等
```

### 2.3 仅 A（父目录）存在 —— 28 个文件

**全部可安全丢弃**，归为三类：

| 类别 | 文件 | 丢弃理由 |
|---|---|---|
| 遗留 shim 层 | `scripts/{utils,models,notifier,output,recommend,archive,ai_processor,logging_config,topic_filter,translate}.py`、`scripts/fetchers/*.py` | 1–6 行的重导出，B 已重构掉；实现都在 `core/` |
| 管道状态/缓存 | `data/archive.json`、`data/title-zh-cache.json` | 按设计由 `pipeline-state` 分支维护，且已 gitignore |
| 垃圾 | `logs_75992604029.zip`、`temp_logs/`、`scratch/`、`.claude/settings.local.json` | CI 日志压缩包、临时日志、个人编辑器配置 |

---

## 三、合并方案（已执行完毕）

**以 B 为基准重建仓库根**，然后移植修复。实际执行：

1. ✅ 备份 A 的 `.git` 与 B 的源码 → `../_radar_backup_20261001_205900/`
   （含 `git_dir/`、`copy/`、`parent_src/` 三份，**未删除，可随时还原**）
2. ✅ 把 A 的旧源码全部移到 `../_radar_backup_20261001_205900/parent_src/`（保留 `.git`）
3. ✅ 把 B 的源码复制到仓库根
4. ✅ 清理 `__pycache__` / `*.pyc` / `.pytest_cache`
5. ✅ 移植 4 项修复（详见第五节）
6. ✅ 全量验证：**92 passed**，8 个路由全 200

**回滚方法**：备份在 `../_radar_backup_20261001_205900/`。
还原方式：把 `parent_src/` 内容拷回仓库根，并用 `git_dir/` 覆盖 `.git`。

---

## 四、风险评估

| 风险 | 等级 | 处置 |
|---|---|---|
| 误删 A 的独有内容 | 低 | 已确认 28 个文件全部为 shim/垃圾/管道状态 |
| B 的代码引入新问题 | 中 | 合并后跑全量测试 + API 冒烟 → 92 passed / 8×200 ✅ |
| `.git` 被破坏 | 低 | 已单独备份 `.git` ✅ |
| B 的 `docs/` 含误导内容 | 低 | 保留，属于项目文档资产 |
| git 历史出现"删除+新增"大批文件 | 中 | 属预期（重构换代），提交信息中说明 |

---

## 五、移植的修复（4 项）

### P0-1　时区缺失导致 import 即崩
B 的 `core/models.py` 与 A 一样是裸 `SH_TZ = ZoneInfo("Asia/Shanghai")`。
已改为 `_resolve_shanghai_tz()`：加载失败时降级为固定 UTC+8 并打警告；
`requirements.txt` 显式钉 `tzdata==2025.2`。
（中国无夏令时，降级语义等价 —— 属双保险，不是替代）

### P0-2　CI 在 "Commit and push changes" 步骤失败
**（本节结论已更正 —— 早先版本误判为 action 版本号不存在）**

真正原因：`git add` 指向了被 `.gitignore` 排除的路径。GitHub Actions 运行
`36833452328`（main@4afd90f）的日志原文：

```
The following paths are ignored by one of your .gitignore files:
data/archive.json
data/title-zh-cache.json
##[error]Process completed with exit code 1.
```

该次运行的步骤 1–9 全部 success，**仅第 10 步失败** —— 也就是说前 9 步的活
全白干了。Git 对被忽略的路径执行 `git add` 会直接 exit 1。

另有一条独立的失败：历史大量 `cancelled` 的运行全部卡在第 7 步 "Update data"、
耗时恰好 `20m19s`/`20m20s`，即被 `timeout-minutes: 20` 强杀（实测 pipeline 需
约 20 分 20 秒）。

修正内容：
- `git add` 去掉 `data/archive.json`、`data/title-zh-cache.json`
- 提交前判空改用 `git diff --cached --quiet`（原 `git diff --quiet` 在 add 之后恒为真）
- `timeout-minutes` 20 → 40
- validate 步骤改用独立脚本

> **更正说明**：早先版本称 `checkout@v6` / `setup-python@v6`「从未发布」。
> 经 GitHub tags API 核实**该说法错误** —— 两个 tag 都真实存在
> （checkout 有 v1…v7，setup-python 有 v1…v7），且失败运行的 Checkout /
> Setup Python 步骤均为 success。版本号从来不是失败原因。

### P0-3　`/hot` 契约漂移永久 503
`core/output.py` 给 `latest-24h-all.json` 写的是 `items_all`，而 `api/app.py::_items_of`
只查 `items_ai` / `items`。加上 `_load_json` 返回非空 dict 使 `all or main` 短路，
永不回退 → `/hot` 恒 503。已补 `items_all` 并按「实际条目数」判空回退。

### 新增资产
- `scripts/validate_data.py`：独立数据门禁（结构 + 条目数 + 陈旧度 + 信源成功率），失败 exit 1
- `tests/test_p0_regressions.py`：18 个回归测试，锁死上述失败模式
  （其中 `test_workflow_never_git_adds_ignored_paths` 会读 `.gitignore` 交叉校验
  workflow 的 `git add` 目标；已实测把旧写法注入回去该项会失败）

---

## 六、重建后的验证证据

```
pytest tests/                 → 94 passed
/health /daily-report /daily-report/markdown /trends /items /stats /hot  → 全 200
4 个 workflow YAML             → 全部可解析
action 引用                    → 均固定到具体大版本
对已删 shim 的悬空 import      → 0
scripts/update_news.py --help  → 正常
```

---

## 七、遗留

- **一个删不掉的空目录**：`ai-news-radar-enhanced-main/`（内容已清空）。
  删除时报 `SHARING_VIOLATION`，句柄枚举确认无进程持有 → 孤儿句柄。
  已在 `.gitignore` 中忽略，不会污染提交；**重启后 `rmdir` 一次即可**。
- `data/` 里的快照数据陈旧约 921 小时，需要触发一次 Actions 或本地跑
  `scripts/update_news.py` 刷新。


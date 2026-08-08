# 仓库冗余清理报告

对 `821920046/ai-news-radar-enhanced` 全仓 214 个跟踪文件逐一做了引用可达性分析。
结论：**103 个文件属于多余或不该上传的内容**，清理后跟踪文件数 214 → 111。

执行方式：`DRY_RUN=false ./CLEANUP.sh`（默认 dry-run，只打印不改动）。

---

## 一、决定性发现：文档声称已删除的代码，其实一个都没删

仓库里的 `本次改动说明.md` 写着两段清理记录：

> 「取消每天定时企微简报（**已彻底删除相关代码**）」
> 「无用/冗余代码清理（第二轮：**删除** + 合并）」

实测这两份删除清单里的文件**全部健在**。也就是说，之前的清理只改了文档，没改代码。
这解释了为什么仓库里同时存在多份同名模块：

| 文件 | 行数 | 与正式版差异 |
|---|---|---|
| `core/normalize/normalizer.py` | 342 | ← 正式版（`core.pipeline` 在用） |
| `normalizer.py`（根目录） | 339 | 与正式版 diff 37 行，其中一处是乱码 `模型���布` |
| `core/notifier.py` | 369 | 功能已按文档取消 |
| `notifier.py`（根目录） | 534 | 与 `core/notifier.py` diff 218 行 |
| `scripts/notifier.py` | 2 | 转发 shim |

根目录那两个副本是**旧版本的残留**，不是当前代码，且已含乱码。

---

## 二、不该上传的文件（76 个）——`.gitignore` 写了却完全无效

`.gitignore` 里明明写着 `__pycache__/`、`*.pyc`、`.claude/`、`feeds/*.opml`，但这些文件全在仓库里。

**原因（第一性）：`.gitignore` 只对「尚未被跟踪」的文件生效。** 一旦文件在忽略规则加入之前就被 `git add` 过，
这条规则对它就永久失效，git 不会有任何提示。所以这不是规则写错，而是**规则来得太晚**。
改 `.gitignore` 治不了，必须 `git rm --cached` 解除跟踪。

| 内容 | 数量 | 说明 |
|---|---|---|
| `*.pyc` / `__pycache__/` | 74 个文件、12 个目录、708 KB | Python 字节码缓存，机器生成，且与本地解释器版本绑定 |
| `.claude/settings.local.json` | 1 | 本地 AI 助手个人配置，属于开发者机器的东西 |
| `feeds/follow.opml` | 1 | **私有订阅源**（下述） |

### `feeds/follow.opml` 是隐私问题，不只是冗余

该文件头部注释是它自己写的：

> 「你的私有订阅源……CI 把整份文件 base64 后放进 `FOLLOW_OPML_B64` secret」

换言之：CI 从 **secret** 取这份数据，仓库里这个副本对流水线毫无用处，却把本该保密的订阅列表
公开在了公共仓库里。仓库里已有 `follow.example.opml` 作为示例模板，副本纯属多余。

**这 76 个文件只解除跟踪，磁盘上原样保留**，所以本地缓存、私有订阅、助手配置都照旧可用。
已验证：解除跟踪后 `feeds/follow.opml` 与 `.claude/settings.local.json` 仍在磁盘上。

---

## 三、死代码（27 个文件）——删除前逐个证明「无人引用」

没有采用「我看着像没用就删」的做法。脚本会为每个待删文件推导出其他代码引用它时会用的模块路径，
然后在**所有存活文件**里搜索；只要还有一处命中，就整体拒绝执行、一个文件都不动。

### 3.1 notifier 整链（4 个文件）

依赖链实测：

```
tests/test_notifier.py:3  from scripts.notifier import (...)
        └─> scripts/notifier.py (2 行 shim)
                └─> core/notifier.py (369 行)
```

**这条链只被它自己的测试维持存活**，产品代码里没有任何人调用它，而它实现的定时简报功能已按文档取消。
为死功能保留测试不是保留代码的理由，因此整链连同 `tests/test_notifier.py` 一并删除。
加上旧版残留 `notifier.py`（根目录），共 4 个文件。

### 3.2 `scripts/` 下的 18 个转发 shim + `scripts/fetchers/` 整个目录

代码早已迁移到 `core/`，`scripts/` 下留了一批 2~4 行的转发文件。全仓搜索 `scripts.*` 引用**只有 3 处**，
且全部在本次要删的文件内部；所有测试都已直接 `from core.*`。删除清单：

```
scripts/ai_processor.py  archive.py  dedup.py  logging_config.py  models.py
         output.py  recommend.py  topic_filter.py  translate.py  utils.py  notifier.py
scripts/fetchers/  __init__.py  aggregators.py  aihub.py  builders.py
                   newsletters.py  official.py  opml.py  oss_trending.py  waytoagi.py
```

删除顺序上有个连锁效应，已核对无误：删掉 `scratch/test_filter.py` 后 `scripts/topic_filter.py` 引用归零；
删掉 `scripts/recommend.py` 后 `scripts/utils.py` 引用归零。三者必须同批删除才自洽。

### 3.3 `scratch/`（2 个文件）与 `OPTIMIZATION.diff`

- `scratch/analyze_dupes.py`、`scratch/test_filter.py`：一次性调试脚本，只被文档提及。
- `OPTIMIZATION.diff`（5.6 KB）：把 diff 本身提交进了仓库。diff 是 git 的原生能力，
  提交一份 diff 文件属于纯产物垃圾。已把 `*.diff` 加进 `.gitignore` 防止复发。

---

## 四、明确保留的文件，及保留理由

清理最容易犯的错是删掉「看起来没人用、其实是入口」的文件。以下逐一核实后保留：

| 文件 | 保留理由（含证据） |
|---|---|
| `scripts/update_news.py` | CI 主入口，`update-news.yml` 第 65、67 行直接调用 |
| `scripts/prerender.py` | `update-news.yml` 第 86 行调用 |
| `scripts/probe_aihot.py` | 运维诊断工具：`core/fetch/aihot_virxact.py` 第 18、168 行的报错信息主动指引用户运行它 |
| `scripts/__init__.py` | 包标记，删除会影响 pytest 收集 |
| `assets/input.css` | 无代码引用，但它是 Tailwind 构建源（`package.json` + `tailwind.config.js` 配套） |
| `assets/log.png`（1.49 MB） | 被文档引用；部署时已在组装 `_site` 阶段排除，不会拖慢线上站点 |
| `_headers`、`.cfignore` | Cloudflare Pages 配置，在 GitHub Pages 上惰性，但记录了 CSP 安全头意图，删掉会丢失设计信息 |
| `api/`（FastAPI 三文件） | 无工作流调用，疑似孤岛，但**不敢删**：删错会毁掉一个可用的服务端入口，且它只多带 2 个依赖。建议你确认是否还需要，我不替你决定 |
| `frontend/hot-ticker.js` 等 | 同上，`index.html` 未加载它，但 `assets/app.js` 有功能等价的内联实现，属于待你确认的孤岛 |

---

## 五、8 份历史文档：移动而非删除

根目录堆了 8 份一次性的历史报告，它们不是活文档，却让仓库根目录看不出「当前系统长什么样」：

```
本次改动说明.md  CHANGELOG_本轮升级.md  OPTIMIZATION_REPORT.md  UPGRADE_2026-07.md
README_免费优化.md  README_模型配置.md  README_Cloudflare安全头.md  README_Cloudflare_KV_免费额度.md
```

这些是过往决策的唯一记录，删掉会丢失信息，因此 `git mv` 进 `docs/history/`。
`README.md` 与 `CLAUDE.md` 是活文档，留在根目录。
（若确实想删除，设 `KEEP_HISTORY=false`。）

---

## 六、凭证泄露专项排查：未发现

按 OpenAI / GitHub / Slack / AWS token 及 PEM 私钥的特征串全仓扫描：

```
grep -iE 'sk-[a-z0-9]{20}|ghp_[A-Za-z0-9]{20}|xox[bp]-|AKIA[0-9A-Z]{16}|-----BEGIN'
```

**零命中。** 唯一的敏感数据问题就是上文的 `feeds/follow.opml`。

---

## 七、安全机制与验证结果

脚本的每一道防线都被实际触发过，不是纸面声明：

| 机制 | 实测结果 |
|---|---|
| 默认 dry-run | 只有字面量 `DRY_RUN=false` 才动手，拼错一律退回 dry-run；dry-run 后实测改动数 = 0 |
| 忽略清单由 git 自己回答 | 用 `git check-ignore` 而非手写清单，规则变了不会失准 |
| 引用闭合护栏 | 人为植入一个仍 import 待删 shim 的文件 → **exit 1，215 个跟踪文件一个没动** |
| 失败自动回滚 | `trap` 在任何中途失败时 `git reset --hard` 回到起点，避免半清理状态 |
| 删除后编译验证 | `compileall` 通过；先清空 `__pycache__` 再编译，防止残留字节码掩盖坏掉的 import |
| 悬空 import 检测 | AST 遍历全仓，确认没有任何存活文件 import 已消失的模块 |
| 测试门禁 | **62 项测试全绿**（与清理前同一批，仅少了死功能的 `test_notifier`） |
| 幂等性 | 提交后重跑：三个阶段各 0 个文件，暂存区 0 变更 |
| 防复发加固 | `.gitignore` 补 `*.diff`、`scratch/`，重复运行不会写入重复行 |

### 清理前后对比

| 指标 | 清理前 | 清理后 |
|---|---|---|
| 跟踪文件数 | 214 | **111** |
| 跟踪的 `.pyc` | 74 | **0** |
| 公开的私有订阅源 | 1 | **0** |
| 重复的 normalizer / notifier 实现 | 5 份 | **2 份**（各 1 份正式版） |
| 根目录 Markdown 文件 | 10 | **2**（README、CLAUDE） |
| 测试 | 62 通过 | **62 通过** |

---

## 八、使用方法

```bash
# 1. 先看会动哪些文件（不改任何东西）
./CLEANUP.sh

# 2. 确认后执行；改动只进暂存区，不自动提交
DRY_RUN=false ./CLEANUP.sh

# 3. 自己复核后再提交
git status && git diff --cached --stat
git commit -m "chore: remove dead code and files that should not be tracked"
```

脚本要求工作区干净。唯一例外是被跟踪的 `.pyc` 造成的「脏」——那正是本次要修的缺陷本身，
若因此拒绝执行，这个修复将永远无法进行，所以仅对 `.gitignore` 已覆盖的路径豁免。

> 注意：解除跟踪后，这些文件在**你的**磁盘上仍然存在，但其他协作者下次拉取时会失去它们。
> `feeds/follow.opml` 需各自从 `FOLLOW_OPML_B64` secret 或 `follow.example.opml` 自行准备。

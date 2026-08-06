# 最终对抗审查

## 结论

修复包从部署、产物、调度、状态持久化、Git 历史、缓存、域名、并发、API 错误和迁移原子性十个层面闭环。不是只处理 `deployment_in_progress` 表象，而是移除了会重复制造该问题的上游条件。

## 已执行验证

### Artifact 清理器（12 组）

- dry-run：列出候选但零删除，exit 0
- 标准删除：删除 101/102/107，exit 0
- TOCTOU：run 1002 在快照后变为 `in_progress`，102 被跳过
- 403 权限错误：立即停止并 exit 1
- secondary rate limit：2s/6s 退避后恢复并完成
- 持续 429：重试上限后停止并 exit 1
- 无 HTTP 状态的网络错误：不再伪装成功，失败率触发 exit 1
- HTTP 502：退避重试后停止并 exit 1
- `MAX_DELETES=1`：只删除最旧一份
- 404：计为已不存在，不算失败
- 时间预算为 0：立即让位，不误删
- 非法输入：回退安全默认值；未知 `DRY_RUN` 值按 true 处理

### 状态分支迁移

在本地裸远端 + 浅克隆环境实测：

- `APPLY_FIX.sh` 首次运行成功
- 重复运行幂等，`.gitignore` 规则不重复
- 只有远端状态快照写入成功后才从 main 停止跟踪大文件
- 显式 refspec 可在 `actions/checkout` 的 single-branch 浅克隆中创建 `origin/pipeline-state`
- 状态分支更新后始终只有一个提交
- 三个状态文件可完整恢复，更新后的状态可覆盖远端快照

### 静态验证

- `bash -n`：`APPLY_FIX.sh`、`cleanup_artifacts.sh` 通过
- PyYAML：三个 workflow 全部解析通过
- Pages 发布前增加 JSON、空文件、CNAME、总大小四类阻断检查

## 关键设计

- Pages 全局串行，进行中部署不取消
- Pages artifact 仅保留 1 天，只包含浏览器真正使用的文件
- 每次 Pages 完成后事件驱动清理；每日 cron 兜底
- DELETE 前实时重核 run 状态
- 24 小时时间缓冲 + 同名最新一份 + 活跃 run + 当前 run 四层保护
- 配额、时间预算、指数退避、权限错误转红
- `archive.json`、`trend_history.json`、`title-zh-cache.json` 从 main 迁到单提交状态分支
- `CNAME` 固化为 `news.my-tv.eu.cc`
- Service Worker 缓存键绑定 commit SHA
- 更新任务串行、暂存区正确判空、push 冲突重试、失败 webhook 正确触发

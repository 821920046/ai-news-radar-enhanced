# 一次性修复包

## 应用

在仓库根目录解压本包（覆盖同名文件），然后执行：

```bash
chmod +x APPLY_FIX.sh
./APPLY_FIX.sh
git diff --cached --stat
git commit -m "fix: harden Pages deployment and Actions storage"
git push
```

`APPLY_FIX.sh` 会先把 `archive.json`、`trend_history.json`、`title-zh-cache.json` 安全迁移到只有一个提交的 `pipeline-state` 分支；只有远端状态分支写入成功后，才会从 `main` 停止跟踪这些文件。任何一步失败都会立即退出，不会造成状态丢失。

## 部署顺序

1. 推送修复提交。
2. 手动运行 **Update AI News Snapshot** 一次，确认采集、状态分支和 push 成功。
3. 手动运行 **Deploy to GitHub Pages** 一次。
4. 手动运行 **Cleanup Actions Artifacts**，第一次保持 `dry_run=true`；核对 Summary 后再以 `dry_run=false` 运行。
5. 此后：内容更新按小时串行运行；Pages 仅在站点文件变化时部署；每次 Pages 完成后事件驱动清理一小批 artifact；每日定时任务负责兜底清理。

## 已封闭的问题

- Pages deployment 被取消后产生孤儿部署：全局串行且禁止取消进行中的部署。
- artifact 过大：只组装浏览器真正读取的文件，排除历史状态和无引用图片。
- artifact 长期堆积：源头保留 1 天 + 事件驱动清理 + 每日兜底。
- 删除竞态：候选快照后、DELETE 前再次读取 run 的实时状态。
- 误删：活跃 run、本次 run、时间缓冲带、同名最新版本四层保护。
- API 限流和权限错误：分类、指数退避、配额、时间预算、失败转红。
- 定时任务延迟：增加 `workflow_run` 事件触发，不再只依赖 cron。
- 自定义域名丢失：每份 Pages artifact 都包含 `CNAME`。
- Service Worker 长期返回旧数据：缓存键注入 commit SHA。
- 坏 JSON/空文件/意外大包：发布前阻断。
- update workflow 暂存区判空错误、push 冲突、webhook 恒假、表达式损坏：均已修复。
- 仓库每小时写入约 18MB 可变 JSON：迁移到单提交状态分支，停止 main 历史膨胀。

## 文件

- `.github/workflows/deploy-pages.yml`
- `.github/workflows/update-news.yml`
- `.github/workflows/cleanup-artifacts.yml`
- `scripts/cleanup_artifacts.sh`
- `APPLY_FIX.sh`
- `tests/`：离线对抗测试夹具

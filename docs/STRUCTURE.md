# 项目结构

本文描述重构后的目录职责。一条原则：**每个目录只有一种东西，每种东西只在一个目录。**

| 目录 | 放什么 | 不放什么 |
|------|--------|----------|
| `core/` | 全部业务实现 | 任何可执行入口 |
| `scripts/` | 可执行入口（`update_news.py` / `prerender.py` / `probe_aihot.py`） | 库代码、shell 脚本 |
| `tools/ci/` | CI 运维 shell 脚本 | Python 业务代码 |
| `config/` | 全部配置（三个 YAML + `topic_rules.json`） | 代码 |
| `data/` | 前端会 fetch 的 5 个 JSON | 管道状态文件 |
| `tests/` | 业务单测；`tests/ci/` 为 CI 脚本的测试替身 | 被测代码 |
| `docs/` | 文档；`history/` 历史记录，`maintenance/` 运维诊断 | 根目录的重复副本 |
| `examples/` | 与主站无依赖关系的独立组件 | 上线代码 |
| `api/` | 可选 FastAPI 服务 | 静态站资源 |
| 仓库根 | GitHub Pages 必须在根的站点文件（`index.html` / `sw.js` / `manifest.json` 等） | 文档、脚本、游离模块 |

## 为什么 `data/` 里只剩 5 个文件

`archive.json`（16MB）、`trend_history.json`、`title-zh-cache.json` 是**管道状态**，不是源码。
`update-news.yml` 每次运行都会从 `pipeline-state` 分支恢复它们，结束时再写回去（搜 `Restore pipeline state`
与 `Save pipeline state` 两个步骤）。把它们放在主干会让每小时一次的 CI 在历史里堆出巨量二进制 diff。
它们已写入 `.gitignore`。

## 为什么 `index.html` 不能挪进子目录

GitHub Pages 从仓库根发布，`deploy-pages.yml` 组装 `_site` 时直接 `cp index.html manifest.json sw.js
robots.txt sitemap.xml _site/`。移动这些文件会同时弄坏发布流程和 Service Worker 的作用域。

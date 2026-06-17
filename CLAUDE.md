# CLAUDE.md — AI News Radar v3

Project: AI 新闻聚合 → 情报系统。Python CLI 管线 + FastAPI + 纯静态前端。

## 目录结构（v3）

```
core/          ← 主引擎（signal_score / trend_engine / agents / pipeline / fetch / normalize / dedup）
api/           ← FastAPI（app.py + routes.py）
config/        ← YAML 配置（sources / score_weights / model_config）
scripts/       ← 向后兼容 shim 层，全部 delegate 到 core/
configs/       ← JSON 配置（topic_rules.json）
data/          ← CI 产出 JSON
frontend/      ← 纯静态 SPA
tests/         ← pytest
```

## 核心模块职责

| 模块 | 路径 | 职责 |
|------|------|------|
| Pipeline | `core/pipeline/main_pipeline.py` | 7 阶段编排器（Fetch → Normalize → Translate → Dedup → Signal Score → AI TL;DR → Output） |
| Signal Score 2.0 | `core/signal_score/scorer.py` | 5 维加权评分（source_weight/technical_score/novelty/velocity/community）→ S/A/B/C |
| Trend Engine | `core/trend_engine/` | Embedding 聚类 + cosine 相似度 + 7 日基线突发检测 |
| Multi-Agent | `core/agents/` | fetch/analyst/trend/editor/critic 五个 Agent，自动生成日报 |
| Fetch | `core/fetch/` | 13 信源并发抓取（30+ RSS feed + 9 聚合站 + GitHub Trending） |
| Normalize | `core/normalize/normalizer.py` | AI 关键词过滤、主题分类（AI/科技/数码/硬件/开源热榜）、标签标注 |
| Dedup | `core/dedup/deduplicator.py` | URL 精确 → 标题极净 → Bigram Jaccard 模糊，三阶段级联 |
| Translate | `core/normalize/translator.py` | Google Translate API EN→ZH，断崖式缓存保护 |
| Recommend | `core/recommend.py` | 推荐理由生成 + signal_score（60-99 兼容旧格式） |
| Notifier | `core/notifier.py` | 企业微信/钉钉/飞书 Webhook 推送 |
| API | `api/app.py` | FastAPI，6 个端点（daily-report/trends/items/stats/health） |

## 开发命令

```bash
# 运行测试
python -m pytest tests/ -v --tb=short

# 跳过需要 API key 的测试
python -m pytest tests/ -v --tb=short -k "not test_process_items_only_selected_top_n"

# 运行 Pipeline（干跑验证）
python core/pipeline/main_pipeline.py --window-hours 1

# 完整运行
python scripts/update_news.py --window-hours 24 --archive-days 3

# Trend Engine（需要 OPENROUTER_KEYS）
python core/pipeline/main_pipeline.py --window-hours 24 --trend-engine

# FastAPI
uvicorn api.app:app --host 0.0.0.0 --port 8000 --reload

# 安装依赖
pip install -r requirements.txt
```

## 编码约定

- `from __future__ import annotations` 放在每个 .py 文件顶部
- Import 路径：`core/` 下文件用 `from core.xxx import ...`（不用 `from scripts.xxx`）
- `scripts/` 下文件全部是 shim，`from core.xxx import *` 重导出
- 日志：每个模块 `logger = logging.getLogger(__name__)`
- 类型提示：使用现代语法（`list[dict]` 而非 `List[Dict]`）
- 中文 docstring 描述业务逻辑，英文描述技术细节
- 私有函数/变量以 `_` 开头（如 `_env_int`、`_pick_best_item`）

## Feature Gates（环境变量）

| 变量 | 默认 | 说明 |
|------|------|------|
| `AI_TLDR_ENABLED` | 空（关闭） | 设为 `1` 开启 TL;DR |
| `TREND_ENGINE_ENABLED` | 空（关闭） | 设为 `1` 开启 Trend Engine |
| `OPENROUTER_KEYS` | 空 | 逗号分隔的 API Key |

## GraphQL 边界

- 不要修改 `docs/` 目录（产品文档）
- 不要修改 `skills/` 目录（Agent Skill 定义）
- 前端代码（index.html / assets/）不在此工作范围
- `.gitignore` 中的 `feeds/follow.opml` 和 `data/` 不提交
- 不要提交 `.pyc` 缓存文件

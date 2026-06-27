# OpenRouter 多模型配置（中文优先 + 自动回退）

本补丁把原来写死的单一模型（且原默认值 `google/gemma-4-31b-it:free` **并不存在**，会导致
TL;DR 与 AI 翻译全部静默失效）改造为一条「中文效果优先 + 自动回退」的多模型链。

## 默认配置的 5 个免费模型（按优先级）

| 顺序 | 模型 slug | 说明 |
|---|---|---|
| 1 | `deepseek/deepseek-chat-v3-0324:free` | DeepSeek V3：中文摘要/翻译综合最佳，速度快 |
| 2 | `qwen/qwen3-235b-a22b:free` | 通义千问3 235B：中文能力顶级 |
| 3 | `z-ai/glm-4.5-air:free` | 智谱 GLM-4.5-Air：中文强、低延迟 |
| 4 | `moonshotai/kimi-k2:free` | 月之暗面 Kimi K2：中文长文本强 |
| 5 | `deepseek/deepseek-r1:free` | DeepSeek R1：推理兜底（较慢，放最后） |

## 回退逻辑

- `400/404`（模型名错误/已下线）→ 标记该模型为本轮失效，自动换链中下一个模型（这是修复 gemma-4 不存在问题的核心）。
- `402/403/429`（账号额度/鉴权/免费限流）→ 标记当前 key 为已耗尽并轮换下一个 key（OpenRouter 免费档的 429 多为账号级共享限额，故按 key 处理）。
- 整条链都不可用时，翻译仍会回退到 Google 免费翻译（原有兜底保留）。

## 如何覆盖（无需改代码）

在 GitHub Actions 或本地设置环境变量（逗号分隔，按优先级排列）：

```bash
export OPENROUTER_MODELS="deepseek/deepseek-chat-v3-0324:free,qwen/qwen3-235b-a22b:free,z-ai/glm-4.5-air:free,moonshotai/kimi-k2:free"
```

优先级：`OPENROUTER_MODELS` 环境变量 > `config/sources.yaml` 的 `openrouter_models`
> `OPENROUTER_MODEL`（单个，向后兼容）> 代码内置默认链。

## 修改的文件

- `core/utils.py`：新增 `get_model_chain()` / `mark_model_dead()` 与默认模型链。
- `core/agents/analyst_agent.py`：TL;DR 与深度分析改为按模型链回退。
- `core/normalize/translator.py`：单条/批量 AI 翻译改为按模型链回退。
- `core/pipeline/main_pipeline.py`：从 yaml 读取 `openrouter_models` 注入 `OPENROUTER_MODELS`。
- `config/sources.yaml` / `config/model_config.yaml`：写入 5 个中文优先模型。

## 提示

免费模型的可用性与限流策略经常变动。如果某个 slug 报 404，链会自动跳过它；
你也可以到 https://openrouter.ai/models?max_price=0 查最新的 `:free` 列表后更新上面的环境变量。

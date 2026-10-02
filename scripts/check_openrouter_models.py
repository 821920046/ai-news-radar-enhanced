#!/usr/bin/env python3
"""校验配置里的 OpenRouter 模型是否**仍然在线且仍然免费**。

为什么需要这个脚本
------------------
OpenRouter 会定期撤下 `:free` 变体：模型本体还在（可以付费调用），只是不再免费。
撤下之后，配置里的模型名会变成一个「不存在」的模型，而失败在日志里和
「额度用完了」长得几乎一样，于是整条 AI 翻译链路会**静默失效**。

真实事故：2026-10-02 核对时发现 `DEFAULT_OPENROUTER_MODELS` 里的 5 个模型
（deepseek-chat-v3-0324 / qwen3-235b-a22b / glm-4.5-air / kimi-k2 / deepseek-r1）
的 `:free` 变体**全部已下线**，整条链 100% 返回错误，而流水线一直在「正常」运行。

退出码（刻意区分「有问题」和「查不了」）
---------------------------------------
  0  所有模型都在线且免费
  1  有模型已下线 / 已不再免费（需要人工改配置）
  2  **无法确定** —— 网络不通或 API 异常。绝不把「查不到」当成「没问题」，
     这正是本项目反复踩的「不可观测 ⇒ 不可断言」。

用法
----
    python scripts/check_openrouter_models.py
    python scripts/check_openrouter_models.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MODELS_API = "https://openrouter.ai/api/v1/models"
TIMEOUT = 30


def _load_configured_models() -> list[tuple[str, str]]:
    """返回 [(模型名, 来源)]，来源用于报告里指明该改哪个文件。

    刻意**不去重**：同一批模型名散落在 4 个地方，测试需要看到每一处都写了什么，
    才能发现「改了 yaml 没改代码」这类漂移。报告展示时再调 `_dedupe`。
    """
    configured: list[tuple[str, str]] = []

    # 1) core/utils.py 的静态注册表（代码里的默认值）
    try:
        from core.utils import DEFAULT_OPENROUTER_MODELS

        configured.extend((m, "core/utils.py:DEFAULT_OPENROUTER_MODELS") for m in DEFAULT_OPENROUTER_MODELS)
    except Exception as exc:  # pragma: no cover
        print(f"[WARN] 无法导入 core.utils：{exc}", file=sys.stderr)

    # 2) config/sources.yaml（流水线实际写入 OPENROUTER_MODELS 的来源）
    sources_yaml = ROOT / "config" / "sources.yaml"
    if sources_yaml.exists():
        try:
            import yaml

            data = yaml.safe_load(sources_yaml.read_text(encoding="utf-8")) or {}
            for model in data.get("openrouter_models") or []:
                configured.append((str(model).strip(), "config/sources.yaml:openrouter_models"))
            single = data.get("openrouter_default_model")
            if single:
                configured.append((str(single).strip(), "config/sources.yaml:openrouter_default_model"))
        except Exception as exc:  # pragma: no cover
            print(f"[WARN] 无法解析 config/sources.yaml：{exc}", file=sys.stderr)

    # 3) config/model_config.yaml
    model_yaml = ROOT / "config" / "model_config.yaml"
    if model_yaml.exists():
        try:
            import yaml

            data = yaml.safe_load(model_yaml.read_text(encoding="utf-8")) or {}
            orouter = data.get("openrouter") or {}
            for model in orouter.get("models") or []:
                configured.append((str(model).strip(), "config/model_config.yaml:openrouter.models"))
            if orouter.get("tldr_model"):
                configured.append(
                    (str(orouter["tldr_model"]).strip(), "config/model_config.yaml:openrouter.tldr_model")
                )
        except Exception as exc:  # pragma: no cover
            print(f"[WARN] 无法解析 config/model_config.yaml：{exc}", file=sys.stderr)

    return [(model, origin) for model, origin in configured if model]


def _dedupe(configured: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """去重保序，同名模型只保留第一次出现的来源。"""
    seen: dict[str, str] = {}
    for model, origin in configured:
        seen.setdefault(model, origin)
    return list(seen.items())


def _fetch_free_models() -> tuple[dict[str, dict], dict[str, dict]]:
    """返回 (全部模型, 免费模型) 两张 {model_id: 记录} 表。"""
    resp = requests.get(MODELS_API, timeout=TIMEOUT)
    resp.raise_for_status()
    payload = resp.json()
    models = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(models, list):
        raise ValueError("OpenRouter /models 返回结构异常")

    everything: dict[str, dict] = {}
    free: dict[str, dict] = {}
    for entry in models:
        mid = entry.get("id")
        if not mid:
            continue
        everything[mid] = entry
        pricing = entry.get("pricing") or {}
        try:
            if float(pricing.get("prompt") or 0) == 0 and float(pricing.get("completion") or 0) == 0:
                free[mid] = entry
        except (TypeError, ValueError):
            continue
    return everything, free


def classify(
    configured: list[tuple[str, str]],
    everything: dict[str, dict],
    free: dict[str, dict],
) -> dict[str, list[tuple[str, str]]]:
    """把已配置模型分成 healthy / missing / no_longer_free 三类。

    抽成纯函数是为了能在测试里喂合成数据 —— 分类逻辑（而不是网络）才是这个脚本
    真正需要被测的部分。
    """
    result: dict[str, list[tuple[str, str]]] = {"healthy": [], "missing": [], "no_longer_free": []}
    for model, origin in configured:
        if model not in everything:
            result["missing"].append((model, origin))
        elif model not in free:
            result["no_longer_free"].append((model, origin))
        else:
            result["healthy"].append((model, origin))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 OpenRouter 模型是否仍在线且免费")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    args = parser.parse_args()

    configured = _dedupe(_load_configured_models())
    if not configured:
        print("[ERROR] 没有解析到任何已配置的模型 —— 无法校验。", file=sys.stderr)
        return 2

    try:
        everything, free = _fetch_free_models()
    except Exception as exc:
        print(
            f"[UNKNOWN] 无法获取 OpenRouter 模型清单（{exc}）。\n"
            f"          这不代表配置没问题 —— 只是「查不了」。退出码 2。",
            file=sys.stderr,
        )
        return 2

    buckets = classify(configured, everything, free)
    healthy = buckets["healthy"]
    missing = buckets["missing"]
    no_longer_free = buckets["no_longer_free"]

    if args.json:
        print(json.dumps(
            {
                "total_models": len(everything),
                "total_free_models": len(free),
                "healthy": [m for m, _ in healthy],
                "missing": [m for m, _ in missing],
                "no_longer_free": [m for m, _ in no_longer_free],
            },
            ensure_ascii=False,
            indent=2,
        ))
    else:
        print(f"OpenRouter 在线模型 {len(everything)} 个，其中免费 {len(free)} 个。")
        print(f"已配置模型 {len(configured)} 个：正常 {len(healthy)}，"
              f"已下线 {len(missing)}，已不再免费 {len(no_longer_free)}。\n")
        for model, _origin in healthy:
            print(f"  [OK]      {model}")
        for model, origin in missing:
            print(f"  [MISSING] {model}   ← 来源：{origin}")
        for model, origin in no_longer_free:
            print(f"  [PAID]    {model}   ← 已不再是免费模型，来源：{origin}")
        if missing or no_longer_free:
            print(
                "\n请更新 core/utils.py:DEFAULT_OPENROUTER_MODELS 以及 config/sources.yaml、\n"
                "config/model_config.yaml 中的模型名，改用下面这些当前可用的免费模型：\n"
            )
            for model in sorted(free):
                print(f"    {model}")

    if missing or no_longer_free:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

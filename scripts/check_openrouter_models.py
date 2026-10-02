#!/usr/bin/env python3
"""校验 OpenRouter 模型可用性，并可探测每个 key 的免费额度余量。

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

关于 key 池（`--probe-keys`）
---------------------------
OpenRouter 官方文档（https://openrouter.ai/docs/api_reference/limits）明确写着：

    Making additional accounts or API keys will not affect your rate limits,
    as we govern capacity globally.

免费模型的限额是**平台级**的，不是每 key 一份：

    累计充值额度     每分钟请求     每天请求
    < 10               20            50
    >= 10              20          1000

所以「多开几个账号组成 key 池」**并不能**把额度乘以账号数。`--probe-keys` 会调
`GET /api/v1/key` 把每个账号的 `free_model_daily_requests.remaining` 打出来，
让「额度到底还剩多少」这件事变得可观测，而不是靠猜。

用法
----
    python scripts/check_openrouter_models.py
    python scripts/check_openrouter_models.py --json
    OPENROUTER_KEYS=k1,k2 python scripts/check_openrouter_models.py --probe-keys
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MODELS_API = "https://openrouter.ai/api/v1/models"
KEY_API = "https://openrouter.ai/api/v1/key"
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


def is_chat_capable(entry: dict) -> bool:
    """这个免费模型能不能用来做「文本进 → 文本出」的翻译。

    「pricing 为 0」**不等于**「能翻译」。2026-10-02 的 21 个免费模型里混着非对话
    模型，朴素筛选会把它们放进链里，而它们的失败方式极其隐蔽（返回音频/分类标签，
    不是报错）：

      - `google/lyria-3-*`：`output_modalities` 含 `audio` → 音乐生成模型；
      - `nvidia/nemotron-3.5-content-safety:free`：安全**分类器**，输出标签而非译文。

    因此除了「输出里必须有 text」，还要排除音频输出，并挡掉已知的垂类/审核模型名。
    """
    arch = entry.get("architecture") or {}
    outputs = arch.get("output_modalities")
    if isinstance(outputs, list):
        if "audio" in outputs:
            return False
        if "text" not in outputs:
            return False
    mid = str(entry.get("id") or "").lower()
    return not any(tag in mid for tag in ("content-safety", "moderation", "guard"))


def classify(
    configured: list[tuple[str, str]],
    everything: dict[str, dict],
    free: dict[str, dict],
) -> dict[str, list[tuple[str, str]]]:
    """把已配置模型分成 healthy / missing / no_longer_free / not_a_chat_model 四类。

    抽成纯函数是为了能在测试里喂合成数据 —— 分类逻辑（而不是网络）才是这个脚本
    真正需要被测的部分。
    """
    result: dict[str, list[tuple[str, str]]] = {
        "healthy": [],
        "missing": [],
        "no_longer_free": [],
        "not_a_chat_model": [],
    }
    for model, origin in configured:
        if model not in everything:
            result["missing"].append((model, origin))
        elif model not in free:
            result["no_longer_free"].append((model, origin))
        elif not is_chat_capable(everything[model]):
            result["not_a_chat_model"].append((model, origin))
        else:
            result["healthy"].append((model, origin))
    return result


def _mask(key: str) -> str:
    return f"{key[:8]}...{key[-4:]}" if len(key) > 14 else "short-key"


def probe_keys(keys: list[str]) -> dict[str, Any]:
    """逐个探测 key 的免费额度余量。返回 {key_masked: 状态字典}。

    `GET /api/v1/key` 返回 `free_model_daily_requests.{used,limit,remaining}` 与
    `is_free_tier`。这两个字段是解释 429 的关键：
      - remaining == 0  → 这个账号今天的免费请求用完了（要等 UTC 次日重置）
      - is_free_tier    → 是否从未充值；未充值的账号每日上限只有 50
    """
    report: dict[str, Any] = {}
    for key in keys:
        entry: dict[str, Any] = {"masked": _mask(key)}
        try:
            resp = requests.get(KEY_API, headers={"Authorization": f"Bearer {key}"}, timeout=TIMEOUT)
        except Exception as exc:
            entry["error"] = f"请求失败：{exc}"
            report[entry["masked"]] = entry
            continue
        if resp.status_code != 200:
            entry["error"] = f"HTTP {resp.status_code}"
            report[entry["masked"]] = entry
            continue
        try:
            data = (resp.json() or {}).get("data") or {}
        except ValueError:
            entry["error"] = "响应不是合法 JSON"
            report[entry["masked"]] = entry
            continue

        free = data.get("free_model_daily_requests") or {}
        entry.update(
            {
                "is_free_tier": data.get("is_free_tier"),
                "usage": data.get("usage"),
                "usage_daily": data.get("usage_daily"),
                "limit_remaining": data.get("limit_remaining"),
                "free_daily_used": free.get("used"),
                "free_daily_limit": free.get("limit"),
                "free_daily_remaining": free.get("remaining"),
            }
        )
        report[entry["masked"]] = entry
    return report


def _print_key_probe(report: dict[str, Any]) -> None:
    print("\n── Key 免费额度探测 ─────────────────────────────────────────────")
    total_remaining = 0
    unknown = 0
    for masked, entry in report.items():
        if "error" in entry:
            unknown += 1
            print(f"  [??]      {masked}  {entry['error']}")
            continue
        remaining = entry.get("free_daily_remaining")
        limit = entry.get("free_daily_limit")
        tier = "从未充值" if entry.get("is_free_tier") else "已充值"
        if remaining is None:
            unknown += 1
            print(f"  [??]      {masked}  未返回 free_model_daily_requests（{tier}）")
            continue
        total_remaining += int(remaining)
        flag = "[OK]     " if int(remaining) > 0 else "[EXHAUSTED]"
        print(f"  {flag} {masked}  今日免费请求 {entry.get('free_daily_used')}/{limit}，剩余 {remaining}（{tier}）")
    print(f"  合计今日剩余免费请求：{total_remaining}" + (f"（{unknown} 个 key 状态未知）" if unknown else ""))
    print(
        "\n  提醒：OpenRouter 的免费模型限额是**平台级**的 —— 官方文档明确写着\n"
        "  「Making additional accounts or API keys will not affect your rate limits,\n"
        "    as we govern capacity globally.」多开账号**不会**把额度乘以账号数。\n"
        "  未充值账号每天只有 50 次免费请求；**单个账号累计充值 ≥10 额度后每天 1000 次**。\n"
        "  流水线每小时跑一轮、每轮约需 5-10 次请求，即 120-240 次/天 —— 远超 50。\n"
        "  若 AI 翻译长期 0 条，正解是给**一个**账号充值 ≥10，而不是继续加账号。"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 OpenRouter 模型是否仍在线且免费")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    parser.add_argument(
        "--probe-keys",
        action="store_true",
        help="额外探测 OPENROUTER_KEYS 中每个 key 的免费额度余量（需要环境变量）",
    )
    args = parser.parse_args()

    keys = [k.strip() for k in (os.environ.get("OPENROUTER_KEYS") or "").split(",") if k.strip()]
    key_report: dict[str, Any] = {}
    if args.probe_keys:
        if not keys:
            print("[WARN] 未设置 OPENROUTER_KEYS，跳过 key 探测。", file=sys.stderr)
        else:
            key_report = probe_keys(keys)
            if not args.json:
                _print_key_probe(key_report)

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
    not_chat = buckets["not_a_chat_model"]
    broken = bool(missing or no_longer_free or not_chat)

    if args.json:
        print(json.dumps(
            {
                "total_models": len(everything),
                "total_free_models": len(free),
                "healthy": [m for m, _ in healthy],
                "missing": [m for m, _ in missing],
                "no_longer_free": [m for m, _ in no_longer_free],
                "not_a_chat_model": [m for m, _ in not_chat],
                "keys": key_report,
            },
            ensure_ascii=False,
            indent=2,
        ))
    else:
        print(f"OpenRouter 在线模型 {len(everything)} 个，其中免费 {len(free)} 个。")
        print(f"已配置模型 {len(configured)} 个：正常 {len(healthy)}，"
              f"已下线 {len(missing)}，已不再免费 {len(no_longer_free)}，"
              f"非对话模型 {len(not_chat)}。\n")
        for model, _origin in healthy:
            print(f"  [OK]      {model}")
        for model, origin in missing:
            print(f"  [MISSING] {model}   ← 来源：{origin}")
        for model, origin in no_longer_free:
            print(f"  [PAID]    {model}   ← 已不再是免费模型，来源：{origin}")
        for model, origin in not_chat:
            print(f"  [NOTCHAT] {model}   ← 免费但不是文本→文本对话模型"
                  f"（音乐/分类器等），来源：{origin}")
        if broken:
            print(
                "\n请更新 core/utils.py:DEFAULT_OPENROUTER_MODELS 以及 config/sources.yaml、\n"
                "config/model_config.yaml 中的模型名。注意：「免费」不等于「能翻译」——\n"
                "下面这些当前可用的免费模型里，输出含 audio 的是音乐模型、名字含\n"
                "content-safety 的是审核分类器，都不能用于翻译：\n"
            )
            for model in sorted(free):
                flag = "" if is_chat_capable(free[model]) else "   ← 非对话模型，勿用"
                print(f"    {model}{flag}")

    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())

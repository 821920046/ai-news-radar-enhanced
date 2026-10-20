"""OpenRouter 免费模型的**运行期**健康探测与模型链自动补位。

为什么需要它
------------
OpenRouter 会定期撤下 `:free` 变体（模型本体还在，只是不再免费）。撤下之后
配置里的模型名变成一个「不存在」的模型，而失败在日志里和「额度用完了」长得
几乎一样 —— 于是整条 AI 链路**静默失效**。

2026-10-02 的事故：内置模型链 5 个模型的 `:free` 变体全部下线，AI 翻译 0 条。
2026-10-20 复核（`GET /api/v1/models`，实测 468 个在线模型 / 20 个免费）：
8 个配置模型里 **2 个已下线**（qwen3.8-27b / ling-3.0-flash-sante），
而流水线仍在按原链调用，每次都把这两次尝试烧在死路由上。

`scripts/check_openrouter_models.py` 早已能在 CI 上**报告**这件事，但报告
不等于**修复**：它只写 warning，运行时的 `get_model_chain()` 照样返回原链。
本模块补上缺的那一环 —— 在真正调用 OpenRouter **之前**先问一次
「现在哪些免费模型真的能用」，然后：

  1) 剔除已下线 / 已不再免费 / 不是对话模型的配置项；
  2) 用当前在线且免费的模型**补位**，让链条始终有足够的兜底模型；
  3) 探测本身失败时**原样返回配置链** —— 探针是优化，不是新的单点故障。

三条设计约束（都是踩坑换来的）
------------------------------
1. **探测端点公开、无需 key**：`GET /api/v1/models` 不消耗任何免费额度，
   也不受「多账号组池不能乘以账号数」的平台级口径影响。
2. **探测失败绝不掀翻调用方**：本模块任何异常都不得让翻译 / TLDR 崩掉。
   URL 层的网络守卫在测试期抛的是 `RuntimeError` 而非 `RequestException`，
   所以这里一律 `except Exception`。
3. **墙上时钟有上界**：最多 `PROBE_ATTEMPTS` 次尝试 × 单次
   `OPENROUTER_MODEL_PROBE_TIMEOUT` 秒，绝不重试风暴
   （推论三：worst_case = 次数 × 超时，必须能算出来）。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import requests

logger = logging.getLogger(__name__)

# 公开端点：不需要 Authorization，也不消耗免费额度。
MODELS_API = "https://openrouter.ai/api/v1/models"

DEFAULT_TIMEOUT = 8.0
PROBE_ATTEMPTS = 2          # worst_case = 2 × timeout；多出来的那次留给瞬时抖动
PROBE_BACKOFF = 0.5
DEFAULT_TARGET = 8          # 补位后模型链的目标长度（与 DEFAULT_OPENROUTER_MODELS 一致）
DEFAULT_MAX_PER_PROVIDER = 2  # 同一家托管方最多几个 —— 429 的另一个来源就是上游 provider 拥塞

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("[ModelRegistry] 无效的 %s=%r，使用默认值 %.1f", name, raw, default)
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("[ModelRegistry] 无效的 %s=%r，使用默认值 %d", name, raw, default)
        return default


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    logger.warning("[ModelRegistry] 无效的 %s=%r，使用默认值 %s", name, raw, default)
    return default


def probe_enabled() -> bool:
    """探针总开关。`OPENROUTER_MODEL_PROBE=0` 可整体关掉（逃生口 / 排障用）。"""
    return _flag("OPENROUTER_MODEL_PROBE", True)


def is_chat_capable(entry: dict) -> bool:
    """这个模型能不能用来做「文本进 → 文本出」的翻译 / TLDR。

    「pricing 为 0」**不等于**「能翻译」。实测 2026-10-20 的 20 个免费模型里
    仍混着非对话模型，朴素筛选会把它们放进链里，而它们的失败方式极其隐蔽
    （返回音频 / 分类标签，不是报错）：

      - `google/lyria-3-*-preview`：`output_modalities` 含 `audio` → 音乐生成模型；
      - `nvidia/nemotron-3.5-content-safety:free`：安全**分类器**，输出标签而非译文。

    因此除了「输出里必须有 text」，还要排除音频输出，并挡掉已知的垂类/审核模型名。

    本函数是**唯一实现**：`scripts/check_openrouter_models.py` 从这里导入，
    两处各写一份必然漂移（本项目已有「4 处配置必须一致」的同类教训）。
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


def _provider_of(model_id: str) -> str:
    """`qwen/qwen3.8-27b:free` → `qwen`。用于 provider 分散度统计。"""
    return model_id.split("/", 1)[0] if "/" in model_id else ""


@dataclass
class ProbeResult:
    """一次探测的结果。`ok=False` 时 `error` 必须可读 —— 不可观测就不可断言。"""

    ok: bool
    entries: dict[str, dict] = field(default_factory=dict)  # 免费且可对话的 {id: 原始记录}
    total_models: int = 0
    total_free: int = 0
    error: str = ""
    elapsed: float = 0.0
    attempts: int = 0


@dataclass
class ChainReport:
    """模型链 Reconciliation 的结果，同时用于日志与测试断言。"""

    chain: list[str]
    live: list[str]
    dead: list[str]
    alternates: list[str]
    probe_ok: bool
    probe_error: str = ""

    @property
    def changed(self) -> bool:
        return bool(self.dead or self.alternates)


def _free_entries(payload: Any) -> tuple[dict[str, dict], int, int]:
    """从 `/models` 响应里筛出「免费且可对话」的模型。

    返回 (entries, total_models, total_free)。
    """
    models = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(models, list):
        raise ValueError("OpenRouter /models 返回结构异常")

    free: dict[str, dict] = {}
    total_free = 0
    for entry in models:
        mid = entry.get("id")
        if not mid:
            continue
        pricing = entry.get("pricing") or {}
        try:
            is_free = (
                float(pricing.get("prompt") or 0) == 0
                and float(pricing.get("completion") or 0) == 0
            )
        except (TypeError, ValueError):
            continue
        if not is_free:
            continue
        total_free += 1
        if is_chat_capable(entry):
            free[mid] = entry
    return free, len(models), total_free


def probe_free_models(
    session: "requests.Session | None" = None,
    timeout: "float | None" = None,
) -> ProbeResult:
    """拉取 OpenRouter 在线模型清单，筛出当前**免费且可对话**的那些。

    失败（网络 / 非 200 / 结构异常）返回 `ok=False` 并带上原因，绝不抛异常。
    """
    sess = session if session is not None else requests.Session()
    budget = timeout if timeout is not None else _env_float(
        "OPENROUTER_MODEL_PROBE_TIMEOUT", DEFAULT_TIMEOUT
    )
    started = time.monotonic()
    last_error = ""
    attempts = 0

    for attempt in range(1, PROBE_ATTEMPTS + 1):
        attempts = attempt
        try:
            resp = sess.get(MODELS_API, timeout=budget)
            resp.raise_for_status()
            entries, total, total_free = _free_entries(resp.json())
            return ProbeResult(
                ok=True,
                entries=entries,
                total_models=total,
                total_free=total_free,
                elapsed=time.monotonic() - started,
                attempts=attempt,
            )
        except Exception as exc:  # 含测试期 URL 守卫抛的 RuntimeError
            last_error = f"{type(exc).__name__}: {exc}"[:300]
            logger.warning(
                "[ModelRegistry] 第 %d/%d 次模型清单探测失败：%s",
                attempt, PROBE_ATTEMPTS, last_error,
            )
            if attempt < PROBE_ATTEMPTS:
                time.sleep(PROBE_BACKOFF)

    return ProbeResult(
        ok=False,
        error=last_error,
        elapsed=time.monotonic() - started,
        attempts=attempts,
    )


def refresh_model_chain(
    chain: list[str],
    probe: ProbeResult,
    target: "int | None" = None,
    max_per_provider: "int | None" = None,
    exclude: "set[str] | frozenset[str] | None" = None,
) -> ChainReport:
    """按探测结果重整模型链：剔除死模型 + 用在线免费模型补位。

    纯函数（不碰网络、不读环境以外的状态），便于测试喂合成数据。

    四条不变式：
      1. 探测失败 → **原样返回**，一个模型都不动；
      2. 结果永不为空 —— 全死且无补位时退回原链（与 `get_model_chain` 里
         「即使全部 dead 也返回原链以免误杀彻底失效」同一哲学）；
      3. 已配置且在线的模型**永远保留**（补位只往末尾追加，不替换配置）；
      4. `exclude` 里的模型一律不用 —— 那是**运行期**已经拿到 HTTP 400/404 的
         模型。静态清单说它在线，但它对本轮就是死的；运行时证据强于静态快照，
         否则它会被补位逻辑原样请回来（实测踩到过）。
    """
    configured = list(chain or [])
    banned = set(exclude or ())
    if not probe.ok:
        # 查不到 ≠ 没问题。保持原链，让上层照旧运行（这就是探针接入前的行为）。
        return ChainReport(
            chain=configured,
            live=[m for m in configured if m in probe.entries and m not in banned],
            dead=[m for m in configured if m not in probe.entries or m in banned],
            alternates=[],
            probe_ok=False,
            probe_error=probe.error,
        )

    live = [m for m in configured if m in probe.entries and m not in banned]
    dead = [m for m in configured if m not in probe.entries or m in banned]

    target_size = target if target is not None else _env_int(
        "OPENROUTER_MODEL_PROBE_TARGET", DEFAULT_TARGET
    )
    cap = max_per_provider if max_per_provider is not None else _env_int(
        "OPENROUTER_MODEL_PROBE_MAX_PER_PROVIDER", DEFAULT_MAX_PER_PROVIDER
    )

    used_providers: Counter[str] = Counter(_provider_of(m) for m in live)
    skip: set[str] = set(configured) | banned
    alternates: list[str] = []
    for mid, entry in sorted(
        probe.entries.items(),
        key=lambda kv: (-(kv[1].get("context_length") or 0), kv[0]),
    ):
        if len(live) + len(alternates) >= target_size:
            break
        if mid in skip:
            continue
        provider = _provider_of(mid)
        if cap > 0 and used_providers[provider] >= cap:
            continue
        used_providers[provider] += 1
        alternates.append(mid)

    resolved = live + alternates
    if not resolved:  # 不变式 2：宁可用回原链，也不能返回空链
        resolved = configured
    return ChainReport(
        chain=resolved,
        live=live,
        dead=dead,
        alternates=alternates,
        probe_ok=True,
    )


# ── 进程内 memo ────────────────────────────────────────────────────────────
# 一轮流水线里 `get_model_chain()` 会被 translator 与 notifier 多次调用，
# 探测必须只发生一次；同时探测结果要被不同入参的 chain 复用（探的是模型清单，
# 与入参无关）。
_PROBE_CACHE: ProbeResult | None = None
_LAST_LOG_SIGNATURE: "tuple | None" = None
_LOCK = threading.Lock()


def probe_once() -> ProbeResult:
    """进程内只探测一次。测试用 `reset_caches()` 清空。"""
    global _PROBE_CACHE
    with _LOCK:
        if _PROBE_CACHE is None:
            _PROBE_CACHE = probe_free_models()
        return _PROBE_CACHE


def reset_caches() -> None:
    """清空探测与日志 memo（测试隔离用）。"""
    global _PROBE_CACHE, _LAST_LOG_SIGNATURE
    with _LOCK:
        _PROBE_CACHE = None
        _LAST_LOG_SIGNATURE = None


def resolve_model_chain(
    chain: list[str],
    exclude: "set[str] | frozenset[str] | None" = None,
) -> list[str]:
    """探测 + 重整，返回可直接使用的模型链。

    本函数是 `core.utils.get_model_chain()` 的最后一环。它**永不抛异常**：
    探测挂了、补位逻辑出现意外，都退回到传入的链 —— 模型链健康度是一个优化项，
    不能反过来把翻译 / 新闻抓取拖死。

    `exclude` 接收运行期已判定不可用的模型（`core.utils._DEAD_MODELS`）：
    静态清单说它们在线，但本轮它们已经用 400/404 证明过自己不可用了。
    """
    try:
        resolved_input = list(chain or [])
        if not resolved_input or not probe_enabled():
            return resolved_input

        probe = probe_once()
        report = refresh_model_chain(resolved_input, probe, exclude=exclude)
        _log_once(report, probe)
        return report.chain
    except Exception as exc:  # pragma: no cover - 防御性兜底
        logger.warning(
            "[ModelRegistry] 模型链健康探测异常，保持配置原样：%s: %s",
            type(exc).__name__, exc,
        )
        return list(chain or [])


def _log_once(report: ChainReport, probe: ProbeResult) -> None:
    """相同结论只记一次日志 —— 一轮流水线里 reconcile 会被调用多次。

    这里的 memo 刻意是**单个**「上次记过什么」槽位而不是按入参索引的缓存：
    按入参缓存会在 `exclude` 变化时（运行期不断有新模型被判死）返回陈旧判定，
    那正是本项目反复出现的「算了却不用 / 用了却已过期」。
    """
    global _LAST_LOG_SIGNATURE
    signature = (
        probe.ok,
        probe.error,
        tuple(report.live),
        tuple(report.dead),
        tuple(report.alternates),
    )
    with _LOCK:
        if _LAST_LOG_SIGNATURE == signature:
            return
        _LAST_LOG_SIGNATURE = signature
    _log_report(report, probe)


def _log_report(report: ChainReport, probe: ProbeResult) -> None:
    """把探测与补位的结果写进日志 —— 事后取证的主战场就是日志。"""
    if not probe.ok:
        logger.warning(
            "[ModelRegistry] OpenRouter 模型清单探测失败（%d 次尝试 %.2fs：%s）"
            "—— 无法确认哪些模型在线，模型链保持配置原样。这不是「没问题」，只是「查不了」。",
            probe.attempts, probe.elapsed, probe.error or "未知原因",
        )
        return

    if report.dead:
        logger.warning(
            "[ModelRegistry] 探测到 %d 个已配置模型当前不可用（已下线/不再免费/非对话模型），"
            "本轮自动剔除：%s",
            len(report.dead), ", ".join(report.dead),
        )
    if report.alternates:
        logger.info(
            "[ModelRegistry] 已用当前在线且免费的模型补位 %d 个：%s",
            len(report.alternates), ", ".join(report.alternates),
        )
    if not report.changed:
        logger.info(
            "[ModelRegistry] 模型链 %d 个全部在线且免费（探测 %.2fs，"
            "在线模型 %d 个 / 免费可对话 %d 个）",
            len(report.chain), probe.elapsed, probe.total_models, len(probe.entries),
        )

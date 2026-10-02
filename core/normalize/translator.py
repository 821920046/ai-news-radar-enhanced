"""Title translation (EN->ZH) and bilingual field enrichment.

支持两种翻译后端：
1. OpenRouter AI 翻译（高质量，需要 OPENROUTER_KEYS）
2. Google Translate 免费 API（兜底方案）

v3.1: 增加全局熔断器，429/402/403 连续失败 ≥ MAX_CONSECUTIVE_FAILURES 次后
自动触发熔断，跳过所有后续 AI 调用，平滑降级至 Google Translate。

v3.2: 修复 Google 兜底阶段长时间挂起（曾导致 Stage 3 卡死 39 分钟、整条流水线被
40 分钟超时 kill）。三处根因：
  1) Google 兜底**逐条串行**请求 → 条数 × 单条耗时线性放大；
  2) 复用 create_session() 的 Retry(total=3, backoff_factor=0.8) × timeout=12
     → 单条最坏 ~44s，56 条 ≈ 41 分钟；
  3) 无失败计数、无日志 → 全程静默，属于「不可观测 ⇒ 不可断言」。
对策：Google 也有熔断器（连续失败即永久跳过本轮的 Google 兜底）+ 单条超时收紧为
GOOGLE_TIMEOUT + 全局墙上时钟预算 GOOGLE_BUDGET_SECONDS，超预算即停止兜底并留下日志。

v3.3: 让 OpenRouter 的**多账号 key 池**真正生效。
背景（run 36960192663 实测）：配了多个账号的 key，但 AI 翻译 0 条、全部降级 Google。
四处独立缺陷叠加：
  1) 熔断语义错误 —— `_mark_ai_failure()` 在**每次 HTTP 失败**时自增，连续 3 次就全局
     熔断。10 个账号的池实际只试到第 2 个就被关掉，「池」形同虚设。单次 429 只能
     证明「这个账号额度用完了」，不能证明「整个池不可用」。
  2) 无模型轮换 —— 只认单个 `OPENROUTER_MODEL`，而 `core/utils.get_model_chain()`
     早就实现了模型回退链，translator 却没用；且默认链里 5 个模型的 `:free` 变体
     已被 OpenRouter 全部撤下（静默失效）。
  3) 重试放大 —— OpenRouter 请求继承了 `Retry(total=3, status_forcelist=[429])`，
     一次 429 被放大成 4 次请求：既浪费免费额度，又把状态码藏进
     `ResponseError('too many 429 error responses')`，日志里看不出是哪个 key/模型。
  4) 无时间上界 —— `worst_case = 条数 × 路由数 × 超时`，没有任何一层设限。
对策：`_RouteCursor` 做 (key, model) 笛卡尔积轮询，429/402/403 只淘汰 key、
400/404 只淘汰 model，其余失败两维都不淘汰；熔断只在**所有路由都淘汰**时触发；
失败原因结构化回传（outcome）以便归因；加墙上时钟预算与单批路由尝试上限。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from core.utils import has_cjk, is_mostly_english, normalize_url

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OpenRouter AI 翻译配置
# ---------------------------------------------------------------------------

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_OPENROUTER_MODEL = "google/gemma-4-31b-it:free"

# AI 翻译的系统提示词：要求高质量、自然流畅的中文翻译
AI_TRANSLATE_SYSTEM_PROMPT = (
    "你是专业的中英科技新闻翻译官。将以下英文标题翻译成简洁准确的中文。"
    "要求：1) 保留专有名词原文（如 GPT-5、Claude、OpenAI）；"
    "2) 译文自然流畅，符合中文新闻标题习惯；"
    "3) 只输出翻译结果，不要任何解释或前缀。"
)

# 批量翻译的系统提示词
AI_BATCH_TRANSLATE_SYSTEM_PROMPT = (
    "你是专业的中英科技新闻翻译官。将以下编号的英文标题逐条翻译为简洁准确的中文。"
    "要求：1) 保留专有名词原文（如 GPT-5、Claude、OpenAI）；"
    "2) 译文自然流畅，符合中文新闻标题习惯；"
    "3) 严格按原编号输出，每行一条，格式：编号. 中文译文；"
    "4) 不要任何额外解释。"
)

# AI 翻译 description 的系统提示词
AI_DESC_TRANSLATE_SYSTEM_PROMPT = (
    "你是专业的科技新闻编辑。将以下英文文章描述翻译为精炼的中文摘要（一到两句话，不超过100字）。"
    "要求：1) 保留关键数据和专有名词；"
    "2) 译文自然流畅；3) 只输出翻译结果。"
)

# 批量翻译每批的上限。
# 免费模型的限额是按**请求数**计的（见 OpenRouter limits 文档：未充值账号每天仅 50 次），
# 所以批越大、请求越少、越不容易撞 429。可用 TRANSLATE_BATCH_SIZE 环境变量调大
# （例如 16 可把 80 条标题的请求数从 10 降到 5），代价是单条解析失败时影响面更大。
BATCH_SIZE = max(1, int(os.environ.get("TRANSLATE_BATCH_SIZE") or 8))

# ---------------------------------------------------------------------------
# Google 兜底配置（v3.2）
# ---------------------------------------------------------------------------
# 单条 Google 请求超时。免费端点从 CI 出口常常不可达，宁可快速放弃也不要拖垮流水线。
GOOGLE_TIMEOUT = 5
# 整个 Google 兜底阶段的墙上时钟预算（秒）。超出即停止兜底、留下日志、继续后续阶段。
GOOGLE_BUDGET_SECONDS = float(os.environ.get("GOOGLE_TRANSLATE_BUDGET_SECONDS") or 60)
# Google 连续失败 N 次即熔断（本轮不再尝试 Google），避免 × 条数的线性放大。
GOOGLE_MAX_CONSECUTIVE_FAILURES = 3
# Google 兜底并发度。免费端点无 key、可容忍，用线程池把串行等待压成并行。
GOOGLE_MAX_WORKERS = 8

# ---------------------------------------------------------------------------
# OpenRouter 路由池配置（v3.3）
# ---------------------------------------------------------------------------
# 「路由」= (api_key, model) 的一个组合。免费额度**按账号计**（key 维度），
# 但单个模型的可用性/限流是**按模型计**（model 维度），两个维度都要轮换，
# 可用容量才是 keys × models，而不是 max(keys, models)。
#
# 时间放大：worst_case = 路由数 × 单次超时。所以必须同时有
#   1) 单批路由尝试次数上限（防止一个批次把整轮预算吃光）
#   2) 整个 AI 阶段的墙上时钟预算（防止 80 条 × N 路由线性放大）
AI_TRANSLATE_BUDGET_SECONDS = float(os.environ.get("AI_TRANSLATE_BUDGET_SECONDS") or 240)
# 同一个批次最多换几条路由重试（1 = 不重试，直接交给 Google 兜底）
MAX_ROUTE_TRIES_PER_BATCH = max(1, int(os.environ.get("AI_MAX_ROUTE_TRIES_PER_BATCH") or 3))
# 单条 OpenRouter 请求超时（秒）
OPENROUTER_TIMEOUT = int(os.environ.get("OPENROUTER_TIMEOUT") or 30)
# 是否要求模型关闭思维链。翻译是纯转换任务，思维链既浪费 token 又可能把
# max_tokens 吃光导致 content 为空。取 "auto" 时不发送该字段。
OPENROUTER_REASONING = (os.environ.get("OPENROUTER_REASONING") or "off").strip().lower()

# ---------------------------------------------------------------------------
# 全局熔断器 — 429/402/403 连续失败达阈值后切断所有 AI 翻译
# ---------------------------------------------------------------------------

MAX_CONSECUTIVE_FAILURES = 3  # 连续失败 N 次触发熔断

_ai_circuit_broken = False              # 熔断标志
_ai_consecutive_failures = 0            # 连续失败计数
_circuit_breaker_lock = threading.Lock() # 线程安全

# Google 兜底熔断器（与 AI 熔断器独立：AI 熔断后正是 Google 兜底最吃力的时候）
_google_circuit_broken = False
_google_consecutive_failures = 0
_google_lock = threading.Lock()


def _mark_google_failure() -> bool:
    """记录一次 Google 兜底失败。返回 True 表示 Google 兜底已熔断。"""
    global _google_circuit_broken, _google_consecutive_failures
    with _google_lock:
        _google_consecutive_failures += 1
        if _google_consecutive_failures >= GOOGLE_MAX_CONSECUTIVE_FAILURES:
            if not _google_circuit_broken:
                _google_circuit_broken = True
                logger.warning(
                    "[Google 熔断器] 连续 %d 次 Google 翻译失败，本轮跳过剩余 Google 兜底"
                    "（剩余标题保留英文原标题，不再消耗流水线时间）。",
                    _google_consecutive_failures,
                )
            return True
        return False


def _mark_google_success() -> None:
    global _google_consecutive_failures
    with _google_lock:
        _google_consecutive_failures = 0


def is_google_circuit_broken() -> bool:
    with _google_lock:
        return _google_circuit_broken


def reset_google_circuit_breaker() -> None:
    global _google_circuit_broken, _google_consecutive_failures
    with _google_lock:
        _google_circuit_broken = False
        _google_consecutive_failures = 0


def _mark_ai_failure() -> bool:
    """记录一次 AI 调用失败。返回 True 表示已触发熔断。"""
    global _ai_circuit_broken, _ai_consecutive_failures
    with _circuit_breaker_lock:
        _ai_consecutive_failures += 1
        if _ai_consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            if not _ai_circuit_broken:
                _ai_circuit_broken = True
                logger.warning(
                    "[AI 熔断器] 连续 %d 次 AI 翻译失败，触发全局熔断！"
                    "后续所有 AI 翻译将跳过，降级至 Google Translate。",
                    _ai_consecutive_failures,
                )
            return True
        return False


def trip_circuit_breaker(reason: str = "手动触发") -> None:
    """立即触发全局熔断（例如所有 key 均已标记耗尽时）。"""
    global _ai_circuit_broken
    with _circuit_breaker_lock:
        if not _ai_circuit_broken:
            _ai_circuit_broken = True
            logger.warning("[AI 熔断器] 触发全局熔断原因：%s。后续 AI 翻译跳过。", reason)


def _mark_ai_success() -> None:
    """记录一次 AI 调用成功，重置连续失败计数。"""
    global _ai_consecutive_failures
    with _circuit_breaker_lock:
        _ai_consecutive_failures = 0


def _is_circuit_broken() -> bool:
    """检查熔断器是否已触发。"""
    with _circuit_breaker_lock:
        return _ai_circuit_broken


def reset_circuit_breaker() -> None:
    """重置熔断器（供测试和新 Pipeline 运行时调用）。"""
    global _ai_circuit_broken, _ai_consecutive_failures
    with _circuit_breaker_lock:
        _ai_circuit_broken = False
        _ai_consecutive_failures = 0


# ---------------------------------------------------------------------------
# 翻译缓存管理
# ---------------------------------------------------------------------------

def load_title_zh_cache(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items() if str(k).strip() and str(v).strip()}
    except Exception:
        pass
    return {}


def safeguard_title_zh_cache(title_cache_path: Path, new_cache: dict[str, str]) -> None:
    """检查原缓存文件大小，若新生成的缓存出现断崖式暴跌，拒绝写入并生成备份。"""
    from core.utils import atomic_write_text

    if not title_cache_path.exists():
        return

    try:
        with open(title_cache_path, "r", encoding="utf-8") as old_f:
            old_cache = json.load(old_f)
    except Exception as e:
        logger.warning("Failed to load old cache for safeguard checks: %s", e)
        return

    if isinstance(old_cache, dict) and len(old_cache) > 100:
        if len(new_cache) < len(old_cache) * 0.5:
            bak_path = title_cache_path.with_suffix(".json.bak")
            try:
                atomic_write_text(bak_path, json.dumps(old_cache, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as e:
                logger.warning("Failed to write backup cache file: %s", e)
            raise ValueError(
                f"Translation cache data plummeted suspiciously from {len(old_cache)} to {len(new_cache)} entries! "
                f"Aborted write to protect translation data. Previous cache backed up to {bak_path}."
            )


# ---------------------------------------------------------------------------
# Google Translate 免费 API（兜底方案）
# ---------------------------------------------------------------------------

def _no_retry_session(session: requests.Session) -> requests.Session:
    """返回一个**禁用重试**的 session。

    复用 create_session() 的 Retry(total=3, backoff_factor=0.8) 有两个害处：
      1) 端点不可达时单条耗时 = 3 次重试 × (超时 + 退避) ≈ 4x，N 条串行即线性放大
         （v3.1 曾因此把流水线卡死 39 分钟）；
      2) 429 被 urllib3 吞掉重试 3 次后抛 ResponseError，日志里只剩
         `too many 429 error responses` —— **看不到是哪个 key / 哪个模型被限流**，
         属于「不可观测 ⇒ 不可断言」。重试由上层路由池负责，HTTP 层不该自己重试。
    """
    s = requests.Session()
    adapter = HTTPAdapter(max_retries=Retry(total=0, connect=0, read=0))
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    # 继承调用方 UA / 语言头，保证行为与主 session 一致
    if getattr(session, "headers", None):
        s.headers.update(session.headers)
    return s


def _google_session(session: requests.Session) -> requests.Session:
    """Google 兜底专用 session（禁用重试）。"""
    return _no_retry_session(session)


def _openrouter_session(session: requests.Session) -> requests.Session:
    """OpenRouter 专用 session（禁用重试）。

    429 必须原样暴露给路由池，才能判定「这个 key 的免费额度用完了」并换下一个 key。
    """
    return _no_retry_session(session)


def translate_to_zh_cn(session: requests.Session, text: str) -> str | None:
    s = (text or "").strip()
    if not s:
        return None
    if is_google_circuit_broken():
        return None
    try:
        r = _google_session(session).get(
            "https://translate.googleapis.com/translate_a/single",
            params={
                "client": "gtx",
                "sl": "auto",
                "tl": "zh-CN",
                "dt": "t",
                "q": s,
            },
            timeout=GOOGLE_TIMEOUT,
        )
        r.raise_for_status()
        payload = r.json()
        if not isinstance(payload, list) or not payload:
            _mark_google_failure()
            return None
        segs = payload[0]
        if not isinstance(segs, list):
            _mark_google_failure()
            return None
        translated = "".join(str(seg[0]) for seg in segs if isinstance(seg, list) and seg and seg[0])
        translated = translated.strip()
        if translated and translated != s:
            _mark_google_success()
            return translated
        _mark_google_failure()
    except Exception:
        _mark_google_failure()
        return None
    return None


# ---------------------------------------------------------------------------
# OpenRouter AI 翻译（高质量）— 集成熔断器
# ---------------------------------------------------------------------------

def _get_openrouter_keys() -> list[str]:
    """从环境变量获取 OpenRouter API keys（多账号池，逗号分隔）。

    去重保序：同一个 key 配两遍没有意义，还会让「所有 key 已耗尽」的判定失真。
    """
    keys_str = os.environ.get("OPENROUTER_KEYS", "")
    return list(dict.fromkeys(k.strip() for k in keys_str.split(",") if k.strip()))


def _get_openrouter_models() -> list[str]:
    """返回有序的模型回退链。

    优先级（实现见 core.utils.get_model_chain）：
      显式参数 > OPENROUTER_MODELS（逗号分隔）> OPENROUTER_MODEL（单个，向后兼容）
      > 内置默认链；本轮已判定下线的模型会被自动剔除。
    """
    from core.utils import get_model_chain

    return get_model_chain()


def _ai_translate_enabled() -> bool:
    return (
        os.environ.get("AI_TRANSLATE_ENABLED", "true").strip().lower()
        not in {"0", "false", "no", "off"}
    )


class _RouteCursor:
    """(key, model) 路由游标：轮询取用 + 逐个淘汰 + 全局预算。

    为什么需要「路由」这个概念：免费额度是**按账号**计的（key 维度），而单个模型
    是否在线、是否被限流是**按模型**计的（model 维度）。只轮换其中一个，可用容量
    就只有 max(keys, models)；两个都轮换才是 keys × models。

    - 顺序为 **model 外层、key 内层**：连续两次尝试换 key，先把按账号计的免费额度
      铺满（429 最常见的成因），所有 key 都撞墙后才换模型。
    - 被淘汰的路由直接从队列移除，本轮不再重试，避免把时间花在已知无望的组合上。
    - 提供墙上时钟预算：超出后 `available()` 立即转 False，上层停止 AI 阶段。
    """

    def __init__(self, keys: list[str], models: list[str], budget_seconds: float):
        self.keys = list(keys)
        self.models = list(models)
        self.dead_keys: set[str] = set()
        self.dead_models: set[str] = set()
        self.attempts = 0
        self.hard_failures = 0
        self._deadline = time.monotonic() + max(0.0, budget_seconds)
        self._queue: list[tuple[str, str]] = []
        self._rebuild()

    def _rebuild(self) -> None:
        self._queue = [
            (key, model)
            for model in self.models
            if model not in self.dead_models
            for key in self.keys
            if key not in self.dead_keys
        ]

    def out_of_budget(self) -> bool:
        return time.monotonic() >= self._deadline

    def available(self) -> bool:
        return bool(self._queue) and not self.out_of_budget()

    @property
    def total_routes(self) -> int:
        return len(self.keys) * len(self.models)

    @property
    def live_keys(self) -> int:
        return len(self.keys) - len(self.dead_keys)

    @property
    def live_models(self) -> int:
        return len(self.models) - len(self.dead_models)

    def next_route(self) -> tuple[str, str] | None:
        if not self.available():
            return None
        route = self._queue.pop(0)
        self.attempts += 1
        return route

    def mark_key_exhausted(self, key: str) -> None:
        """429/402/403：这个账号的免费额度本轮已用完 → 换 key。"""
        if key in self.dead_keys:
            return
        self.dead_keys.add(key)
        self.hard_failures += 1
        masked = f"{key[:6]}...{key[-4:]}" if len(key) > 10 else "short-key"
        logger.warning(
            "[AI Route] key %s 额度耗尽/被限流，已淘汰（剩余 key %d/%d）",
            masked, self.live_keys, len(self.keys),
        )
        self._rebuild()

    def mark_model_dead(self, model: str) -> None:
        """400/404：模型名不存在或已下线 → 换模型。"""
        if model in self.dead_models:
            return
        self.dead_models.add(model)
        self.hard_failures += 1
        logger.warning(
            "[AI Route] 模型 %s 不可用（HTTP 400/404），已淘汰（剩余模型 %d/%d）",
            model, self.live_models, len(self.models),
        )
        try:
            from core.utils import mark_model_dead as _mark_dead

            _mark_dead(model)  # 同步给其他消费方（notifier / analyst_agent）
        except Exception:
            pass
        self._rebuild()

    def summary(self) -> str:
        return (
            f"尝试 {self.attempts}/{self.total_routes} 条路由，淘汰 {self.hard_failures} 次；"
            f"可用 key {self.live_keys}/{len(self.keys)}，可用模型 {self.live_models}/{len(self.models)}"
        )


def _apply_route_outcome(
    cursor: _RouteCursor, api_key: str, model: str, outcome: dict[str, Any]
) -> None:
    """把一次请求的结构化失败原因翻译成路由池的淘汰动作。

    关键区别：
      - key_exhausted（429/402/403）→ 淘汰**这个账号**，换 key 就可能恢复；
      - model_error（400/404）      → 淘汰**这个模型**，换模型就可能恢复；
      - 其余（网络抖动 / 5xx / 空内容 / 解析失败）→ 两维都不淘汰，
        因为一次失败不足以断言「这个 key 或这个模型坏了」。它们由单批尝试次数
        上限和全局预算兜住，不会无限重试。
    """
    status = str(outcome.get("status") or "")
    if status == "key_exhausted":
        cursor.mark_key_exhausted(api_key)
    elif status == "model_error":
        cursor.mark_model_dead(model)


def _openrouter_post(
    session: requests.Session,
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    user_text: str,
    max_tokens: int,
    temperature: float,
    timeout: int,
    outcome: dict[str, Any] | None = None,
) -> str | None:
    """向 OpenRouter 发一次请求，返回 message.content；失败返回 None。

    通过 `outcome` 回传**结构化**失败原因（status / http / model），让上层路由池能
    区分「key 被限流」和「模型下线」并做不同的淘汰决策。此前所有失败在日志里长得
    一模一样（一行 warning），既无法归因也无法自动恢复 —— 属于「不可观测 ⇒ 不可断言」。
    """
    if not api_key or not model:
        return None

    referer = os.environ.get("OPENROUTER_HTTP_REFERER") or "https://github.com/LearnPrompt/ai-news-radar"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": referer,
        "X-Title": os.environ.get("OPENROUTER_APP_TITLE") or "AI News Radar",
    }
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if OPENROUTER_REASONING != "auto":
        # 翻译是纯转换任务，不需要思维链；关掉它可避免推理内容吃光 max_tokens
        # 导致 content 为空（推理型模型在短输出任务上最常见的静默失败）。
        payload["reasoning"] = {"enabled": False}

    def _record(status: str, **extra: Any) -> None:
        if outcome is not None:
            outcome.update({"status": status, "model": model, **extra})

    def _send(body: dict[str, Any]):
        try:
            return _openrouter_session(session).post(
                OPENROUTER_API_URL, headers=headers, json=body, timeout=timeout
            )
        except requests.exceptions.RequestException as exc:
            logger.warning("[AI Translate] 请求失败（模型 %s）：%s", model, exc)
            _record("network_error", error=str(exc)[:200])
            return None

    resp = _send(payload)
    if resp is None:
        return None

    # 个别模型不接受 reasoning 字段 → 摘掉后重试一次，而不是把模型误判为下线
    if resp.status_code == 400 and "reasoning" in payload and "reasoning" in (resp.text or "").lower():
        logger.info("[AI Translate] 模型 %s 不支持 reasoning 字段，摘掉后重试一次", model)
        payload.pop("reasoning", None)
        resp = _send(payload)
        if resp is None:
            return None

    if resp.status_code == 200:
        try:
            data = resp.json()
        except ValueError:
            _record("bad_json", http=200)
            return None
        choices = data.get("choices") if isinstance(data, dict) else None
        if not choices:
            _record("empty", http=200)
            return None
        message = choices[0].get("message") or {}
        content = str(message.get("content") or "").strip()
        finish = choices[0].get("finish_reason")
        if not content:
            # 必须把「思维链吃光 max_tokens」和「模型返回垃圾」区分开，否则无法归因
            has_reasoning = bool(message.get("reasoning"))
            _record("empty_reasoning" if has_reasoning else "empty", http=200, finish_reason=finish)
            logger.warning(
                "[AI Translate] 模型 %s 返回空内容（finish_reason=%s，reasoning=%s）",
                model, finish, "有" if has_reasoning else "无",
            )
            return None
        _record("ok", http=200, finish_reason=finish)
        _mark_ai_success()
        return content

    if resp.status_code in {402, 403, 429}:
        # 免费模型上的 429 几乎总是「这个账号今天的免费额度用完了」→ 换 key。
        #
        # 但**不能只靠猜**：OpenRouter 文档说明，平台级限额触发的 429 会带上
        # X-RateLimit-Limit / -Remaining / -Reset，provider 侧的还会带 Retry-After。
        # 把这三个数记下来，「为什么全是 429」才有答案 —— 是每日额度用完了
        # （reset 在次日）还是每分钟限额（reset 只有几秒），两者的处置完全不同。
        limit_info: dict[str, Any] = {}
        resp_headers = getattr(resp, "headers", None) or {}
        for name in ("X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset", "Retry-After"):
            value = resp_headers.get(name)
            if isinstance(value, str) and value:
                limit_info[name] = value
        _record("key_exhausted", http=resp.status_code, **limit_info)
        logger.warning(
            "[AI Translate] 模型 %s 被限流/额度不足（HTTP %d）%s，换下一个 key",
            model,
            resp.status_code,
            "；平台限额 " + str(limit_info) if limit_info else "（响应未带 X-RateLimit-* 头）",
        )
        return None

    if resp.status_code in {400, 404}:
        _record("model_error", http=resp.status_code, body=(resp.text or "")[:200])
        logger.warning(
            "[AI Translate] 模型 %s 报 HTTP %d：%s", model, resp.status_code, (resp.text or "")[:200]
        )
        return None

    _record("server_error", http=resp.status_code, body=(resp.text or "")[:200])
    logger.warning(
        "[AI Translate] 模型 %s 返回 HTTP %d：%s", model, resp.status_code, (resp.text or "")[:200]
    )
    return None


def _resolve_model(model: str | None) -> str:
    """解析本次请求使用的模型：显式指定 > 模型链首个可用 > 兜底常量。"""
    if model:
        return model
    chain = _get_openrouter_models()
    return chain[0] if chain else DEFAULT_OPENROUTER_MODEL


def _ai_translate_single(
    session: requests.Session,
    text: str,
    api_key: str,
    *,
    model: str | None = None,
    system_prompt: str = AI_TRANSLATE_SYSTEM_PROMPT,
    max_tokens: int = 120,
    timeout: int = 15,
    outcome: dict[str, Any] | None = None,
) -> str | None:
    """通过 OpenRouter API 翻译单条文本。集成熔断器。

    注意：这里**不再**调用 `_mark_ai_failure()`。单次 HTTP 失败不能证明「整个
    key 池都不可用」—— 那正是旧逻辑 3 次失败就全局熔断、把多账号池彻底废掉的
    原因。失败原因经 `outcome` 上抛，由调用方按 key / model 维度分别淘汰。
    """
    if not text or not api_key:
        return None

    # 前置熔断检查
    if _is_circuit_broken():
        return None

    model = _resolve_model(model)
    content = _openrouter_post(
        session,
        api_key=api_key,
        model=model,
        system_prompt=system_prompt,
        user_text=text[:800],
        max_tokens=max_tokens,
        temperature=0.1,
        timeout=timeout,
        outcome=outcome,
    )
    if content is None:
        return None

    # 清理常见前缀。注意：旧写法把 \s 写成了字面量反斜杠+s（raw string 里多打了一个
    # 反斜杠），导致这个正则从来没生效过；这里顺手修掉。
    content = re.sub(r"^(翻译[：:]\s*|译文[：:]\s*)", "", content).strip()
    if content and has_cjk(content):
        return content

    if outcome is not None and outcome.get("status") == "ok":
        # 200 且非空，但输出里没有中文 → 判为无效输出，让上层换路由重试
        outcome["status"] = "invalid_output"
        logger.warning("[AI Translate] 模型 %s 输出不含中文，判为无效：%s", model, content[:80])
    return None


def _ai_translate_batch(
    session: requests.Session,
    titles: list[str],
    api_key: str,
    *,
    model: str | None = None,
    timeout: int = 30,
    outcome: dict[str, Any] | None = None,
) -> list[str | None]:
    """通过 OpenRouter API 批量翻译标题（一次 API 调用翻译多条）。集成熔断器。

    失败原因经 `outcome` 上抛，由调用方按 key / model 维度分别淘汰。
    """
    if not titles or not api_key:
        return [None] * len(titles)

    # 前置熔断检查
    if _is_circuit_broken():
        return [None] * len(titles)

    model = _resolve_model(model)

    # 构建编号列表
    numbered_input = "\n".join(f"{i + 1}. {title}" for i, title in enumerate(titles))

    content = _openrouter_post(
        session,
        api_key=api_key,
        model=model,
        system_prompt=AI_BATCH_TRANSLATE_SYSTEM_PROMPT,
        user_text=numbered_input,
        # 150/条的余量 + 512 的地板：给推理型模型留出思维链空间，
        # 避免 content 还没开始输出就被 max_tokens 截断（表现为「空内容」）。
        max_tokens=max(150 * len(titles), 512),
        temperature=0.1,
        timeout=timeout,
        outcome=outcome,
    )
    if content is None:
        return [None] * len(titles)
    return _parse_batch_result(content, len(titles))


def _parse_batch_result(content: str, expected_count: int) -> list[str | None]:
    """解析批量翻译的编号结果。"""
    results: list[str | None] = [None] * expected_count
    lines = content.strip().split("\n")

    for line in lines:
        line = line.strip()
        if not line:
            continue
        # 匹配格式: "1. 翻译内容" 或 "1、翻译内容" 或 "1) 翻译内容"
        match = re.match(r"^(\d+)[.、)]\s*(.+)$", line)
        if match:
            idx = int(match.group(1)) - 1
            translated = match.group(2).strip()
            # 清理引号包裹
            translated = translated.strip("\"'「」『』")
            if 0 <= idx < expected_count and translated and has_cjk(translated):
                results[idx] = translated

    return results


def _ai_translate_description(
    session: requests.Session,
    desc: str,
    api_key: str,
    *,
    model: str | None = None,
    timeout: int = 20,
    outcome: dict[str, Any] | None = None,
) -> str | None:
    """通过 OpenRouter 将英文 description 翻译为中文精炼摘要。"""
    return _ai_translate_single(
        session,
        desc,
        api_key,
        model=model,
        system_prompt=AI_DESC_TRANSLATE_SYSTEM_PROMPT,
        max_tokens=200,
        timeout=timeout,
        outcome=outcome,
    )


# ---------------------------------------------------------------------------
# 核心入口：双语字段添加（整合 AI 翻译 + Google 翻译兜底）
# ---------------------------------------------------------------------------

def add_bilingual_fields(
    items_ai: list[dict[str, Any]],
    items_all: list[dict[str, Any]],
    session: requests.Session,
    cache: dict[str, str],
    max_new_translations: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    """为文章添加双语标题字段，优先使用 AI 翻译。"""

    # 每次 Pipeline 运行重置熔断器状态（AI + Google 两个熔断器都要复位）
    reset_circuit_breaker()
    reset_google_circuit_breaker()

    # 收集已有的中文标题映射（URL → 中文标题）
    zh_by_url: dict[str, str] = {}
    for it in items_all:
        title = str(it.get("title") or "").strip()
        url = normalize_url(str(it.get("url") or ""))
        if title and url and has_cjk(title):
            zh_by_url[url] = title

    # 获取 OpenRouter API keys 与模型链，构造 (key, model) 路由池
    api_keys = _get_openrouter_keys()
    models = _get_openrouter_models()
    use_ai = bool(api_keys) and bool(models) and _ai_translate_enabled()
    cursor = _RouteCursor(api_keys, models, AI_TRANSLATE_BUDGET_SECONDS) if use_ai else None
    current_key_idx = 0
    ai_translated_count = 0
    google_translated_count = 0

    if use_ai:
        logger.info(
            "[Translate] AI 翻译已启用：%d 个 key × %d 个模型 = %d 条路由，预算 %.0fs，单批最多换 %d 条路由",
            len(api_keys), len(models), len(api_keys) * len(models),
            AI_TRANSLATE_BUDGET_SECONDS, MAX_ROUTE_TRIES_PER_BATCH,
        )
    elif not api_keys:
        logger.info("[Translate] 未配置 OPENROUTER_KEYS，直接使用 Google Translate 兜底")
    elif not models:
        logger.warning("[Translate] 模型链为空（检查 OPENROUTER_MODELS / OPENROUTER_MODEL），直接使用 Google 兜底")
    else:
        logger.info("[Translate] AI 翻译未启用（AI_TRANSLATE_ENABLED），将使用 Google Translate 兜底")

    # 第一轮：收集需要翻译的英文标题
    pending_titles: list[tuple[dict[str, Any], str]] = []  # (item, english_title)

    def _needs_translation(item: dict[str, Any]) -> tuple[bool, str]:
        """检查是否需要翻译，返回 (需要翻译, 英文标题)。"""
        title = str(item.get("title") or "").strip()
        url = normalize_url(str(item.get("url") or ""))

        if has_cjk(title):
            return False, title

        if not is_mostly_english(title):
            return False, title

        # 检查缓存
        zh = zh_by_url.get(url) or cache.get(title)
        if zh:
            return False, title

        return True, title

    # 为 items_ai 收集需要翻译的条目
    for item in items_ai:
        needs, title = _needs_translation(item)
        if needs and len(pending_titles) < max_new_translations:
            pending_titles.append((item, title))

    # -----------------------------------------------------------------------
    # AI 批量翻译：key × model 轮询，逐条路由淘汰
    # -----------------------------------------------------------------------
    # v3.3 之前：单个模型 + 单个 key 游标，且**连续 3 次 HTTP 失败就全局熔断**。
    # 后果是 10 个账号的 key 池实际只用到 2 个，第 3 次 429 就把整轮 AI 翻译关掉
    # （实测 run 36960192663：AI 翻译 0 条，全部降级 Google）。
    # 现在：每次失败只淘汰**出问题的那一维**（429→淘汰 key，400/404→淘汰 model），
    # 只要还有一条路由活着就继续翻；真正无路可走才熔断。
    if use_ai and pending_titles and cursor is not None:
        logger.info("[Translate] AI 批量翻译 %d 条标题...", len(pending_titles))

        # 用「待办队列」而不是固定切片：失败的条目**退回队列**，而不是直接丢弃。
        # 终止性由路由池保证 —— 每轮至少消耗一条路由，池子有限（keys × models），
        # 池空 / 预算耗尽 / 熔断三者任一都会让 while 退出。
        queue: list[tuple[dict[str, Any], str]] = list(pending_titles)

        while queue:
            # 熔断检查：若已触发，立即停止批量翻译
            if _is_circuit_broken():
                logger.warning(
                    "[Translate] 熔断器已触发（%s），剩余 %d 条标题切换到 Google Translate",
                    cursor.summary(), len(queue),
                )
                break

            # 墙上时钟预算：防止「条数 × 路由数 × 超时」把这一步拖到 job 超时被 kill
            if cursor.out_of_budget():
                logger.warning(
                    "[Translate] AI 翻译触及 %.0fs 预算（%s），剩余 %d 条转 Google 兜底",
                    AI_TRANSLATE_BUDGET_SECONDS, cursor.summary(), len(queue),
                )
                break

            if not cursor.available():
                trip_circuit_breaker("所有 key×模型路由均已淘汰")
                logger.warning(
                    "[Translate] 路由全部淘汰（%s），剩余 %d 条切换到 Google Translate",
                    cursor.summary(), len(queue),
                )
                break

            still = queue[:BATCH_SIZE]
            del queue[:BATCH_SIZE]

            # 同一批最多换 MAX_ROUTE_TRIES_PER_BATCH 条路由，避免单批把预算吃光
            for _ in range(MAX_ROUTE_TRIES_PER_BATCH):
                if not still or not cursor.available():
                    break
                route = cursor.next_route()
                if route is None:
                    break
                api_key, model = route
                outcome: dict[str, Any] = {}
                results = _ai_translate_batch(
                    session,
                    [title for _, title in still],
                    api_key,
                    model=model,
                    timeout=OPENROUTER_TIMEOUT,
                    outcome=outcome,
                )
                _apply_route_outcome(cursor, api_key, model, outcome)

                # 只对**这一批里还没拿到译文**的条目换路由重试；已成功的立即落缓存
                failed: list[tuple[dict[str, Any], str]] = []
                for (item, en_title), zh_title in zip(still, results):
                    if zh_title:
                        cache[en_title] = zh_title
                        ai_translated_count += 1
                    else:
                        failed.append((item, en_title))

                if len(failed) < len(still):
                    # 这一批有进展：剩下的退回队尾，先让别的批次推进，别死磕同一批
                    queue.extend(failed)
                    still = []
                    break
                still = failed

            # 换满 MAX_ROUTE_TRIES_PER_BATCH 条路由仍全军覆没 → 也退回队列，
            # 下一轮会拿到新的活路由；池空则整体退出（不会无限循环）。
            queue.extend(still)

            # 控制请求频率，避免触发限流
            time.sleep(0.5)

    # -----------------------------------------------------------------------
    # Google 兜底：**并行预取**（v3.2）
    # -----------------------------------------------------------------------
    # v3.1 在 enrich() 内逐条串行调用 Google，端点不可达时 N 条 × ~44s 线性放大，
    # 直接把 Stage 3 拖到 40 分钟被 kill。这里改为一个并行、有墙上时钟预算、
    # 带独立熔断器的预取阶段：先把能拿到的翻译一次性放进 cache，enrich() 只查表。
    google_budget = max_new_translations - ai_translated_count

    if google_budget > 0:
        # 收集还没有译文的英文标题（去重，保持顺序稳定）
        pending_google: list[str] = []
        seen_pending: set[str] = set()
        for item in items_ai:
            title = str(item.get("title") or "").strip()
            if not title or has_cjk(title) or not is_mostly_english(title):
                continue
            url = normalize_url(str(item.get("url") or ""))
            if zh_by_url.get(url) or cache.get(title):
                continue
            if title in seen_pending:
                continue
            seen_pending.add(title)
            pending_google.append(title)
            if len(pending_google) >= google_budget:
                break

        if pending_google:
            logger.info(
                "[Translate] Google 兜底：待翻译 %d 条，预算 %.0fs，并发 %d。",
                len(pending_google), GOOGLE_BUDGET_SECONDS, GOOGLE_MAX_WORKERS,
            )
            deadline = time.monotonic() + GOOGLE_BUDGET_SECONDS
            skipped_by_budget = 0

            def _fetch(title: str) -> tuple[str, str | None]:
                # 预算 / 熔断 任一先行即短路，避免把时间花在已知无望的请求上
                if is_google_circuit_broken() or time.monotonic() >= deadline:
                    return title, None
                return title, translate_to_zh_cn(session, title)

            try:
                with ThreadPoolExecutor(max_workers=GOOGLE_MAX_WORKERS) as pool:
                    for title, tr in pool.map(_fetch, pending_google):
                        if tr and has_cjk(tr):
                            cache[title] = tr
                            google_translated_count += 1
                        elif time.monotonic() >= deadline and title not in cache:
                            skipped_by_budget += 1
            except Exception as exc:  # 兜底阶段绝不抛出让上层崩掉
                logger.warning("[Translate] Google 兜底并行阶段异常，跳过剩余：%s", exc)

            if is_google_circuit_broken():
                logger.warning(
                    "[Translate] Google 兜底已熔断，本轮成功 %d 条，剩余标题保留英文原标题。",
                    google_translated_count,
                )
            elif skipped_by_budget:
                logger.warning(
                    "[Translate] Google 兜底触及 %.0fs 预算上限，已翻译 %d 条，%d 条超时放弃"
                    "（保留英文原标题）。可调 GOOGLE_TRANSLATE_BUDGET_SECONDS 放宽。",
                    GOOGLE_BUDGET_SECONDS, google_translated_count, skipped_by_budget,
                )
            else:
                logger.info("[Translate] Google 兜底完成，翻译 %d 条。", google_translated_count)

    # 通用 enrich 函数（应用翻译结果）
    def enrich(item: dict[str, Any], allow_translate: bool) -> dict[str, Any]:
        out = dict(item)
        title = str(out.get("title") or "").strip()
        url = normalize_url(str(out.get("url") or ""))

        out["title_original"] = title
        out["title_en"] = None
        out["title_zh"] = None
        out["title_bilingual"] = title

        if has_cjk(title):
            out["title_zh"] = title
            return out

        if not is_mostly_english(title):
            return out

        out["title_en"] = title

        # 查找已有翻译（AI 批量结果 + Google 预取结果都已在 cache 里）
        zh_title = zh_by_url.get(url) or cache.get(title)

        if zh_title:
            out["title_zh"] = zh_title
            out["title_bilingual"] = f"{zh_title} / {title}"

        # 翻译 description（仅对 AI 模式条目、且路由池仍有可用路由时）
        if use_ai and allow_translate and cursor is not None and not _is_circuit_broken() and cursor.available():
            _try_translate_desc(session, out, api_keys, current_key_idx, cursor=cursor)

        return out

    ai_out = [enrich(it, allow_translate=True) for it in items_ai]
    all_out = [enrich(it, allow_translate=False) for it in items_all]

    logger.info(
        "[Translate] 翻译完成：AI 翻译 %d 条，Google 翻译 %d 条，AI 熔断=%s，Google 熔断=%s，缓存命中跳过其余",
        ai_translated_count, google_translated_count,
        "已触发" if _is_circuit_broken() else "正常",
        "已触发" if is_google_circuit_broken() else "正常",
    )
    if cursor is not None:
        logger.info("[Translate] 路由池统计：%s", cursor.summary())

    return ai_out, all_out, cache


def _try_translate_desc(
    session: requests.Session,
    item: dict[str, Any],
    api_keys: list[str],
    start_key_idx: int,
    *,
    cursor: "_RouteCursor | None" = None,
) -> None:
    """尝试将英文 description 翻译为中文（仅在没有 tldr 且 desc 是英文时触发）。

    `cursor` 非空时走路由池，且**只取一条路由**：批量标题阶段已经证明哪条路由能用，
    这里再对每个条目遍历整个 key 池就是典型的「时间放大」
    （80 条 × N key × 15s 可以轻松吃光整轮预算）。
    """
    # 前置熔断检查
    if _is_circuit_broken():
        return

    desc = str(item.get("description") or "").strip()
    if not desc or has_cjk(desc) or len(desc) < 20:
        return

    # 只对没有 tldr 的条目翻译 description
    if item.get("tldr"):
        return

    if not is_mostly_english(desc):
        return

    if cursor is not None:
        if not cursor.available():
            return
        route = cursor.next_route()
        if route is None:
            return
        api_key, model = route
        outcome: dict[str, Any] = {}
        zh_desc = _ai_translate_description(
            session, desc, api_key, model=model, timeout=15, outcome=outcome
        )
        _apply_route_outcome(cursor, api_key, model, outcome)
        if zh_desc:
            item["description"] = zh_desc
            return
    else:
        # 旧路径：保留给直接调用者（含单元测试）
        for idx in range(start_key_idx, len(api_keys)):
            # 循环内也检查熔断
            if _is_circuit_broken():
                break
            zh_desc = _ai_translate_description(session, desc, api_keys[idx], timeout=15)
            if zh_desc:
                item["description"] = zh_desc
                return

    # AI 翻译失败，用 Google Translate 兜底
    try:
        tr = translate_to_zh_cn(session, desc[:300])
        if tr and has_cjk(tr):
            item["description"] = tr
    except Exception:
        pass

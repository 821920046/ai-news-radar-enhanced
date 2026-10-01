#!/usr/bin/env python
"""Operational alerts over the project's existing webhook (WeCom / Slack / generic).

Two independent failure modes are covered, because they have different causes and
require different responses:

1. ``--mode ci`` — the Actions job itself failed, was cancelled, or timed out.
   Someone must look at the run. Without this, a broken pipeline is invisible for
   as long as nobody happens to check the Actions tab.

2. ``--mode stale`` — the job reported success, but the published data has not
   actually advanced. This is the quieter and more dangerous case: the workflow can
   be green every hour while the site keeps serving days-old news. It happens when
   every source fetch fails, when the runner is starved of time, or when an upstream
   API silently degrades. The pipeline exits 0 in all of those cases.

The script never raises on delivery failure: a notification that fails to send must
not turn a successful pipeline run into a failed one.

Usage:
    python scripts/notify_ci.py --mode ci --status failure --data-dir data
    python scripts/notify_ci.py --mode stale --data-dir data --max-age-hours 6

Environment:
    WEBHOOK_URL   destination; when unset the script is a no-op (exit 0)
    WEBHOOK_TYPE  ``wecom`` / ``wechat`` / ``markdown`` (default) or ``slack``/``feishu``
    GITHUB_*      optional, auto-read to enrich the message when running in Actions
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

DEFAULT_TIMEOUT = 10
# 企业微信 markdown 上限约 4096 字节，留出安全余量。
MESSAGE_BYTE_BUDGET = 3500


def _repo_url() -> str:
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    return f"{server}/{repo}" if repo else ""


def _run_url() -> str:
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if not (repo and run_id):
        return ""
    return f"{server}/{repo}/actions/runs/{run_id}"


def _truncate_bytes(text: str, budget: int = MESSAGE_BYTE_BUDGET) -> str:
    """Clamp to ``budget`` UTF-8 bytes, reserving room for the truncation notice.

    The notice itself costs bytes; appending it after slicing would overshoot the
    budget, so the suffix length is subtracted up front.
    """
    suffix = "\n> …（内容过长已截断）"
    encoded = text.encode("utf-8")
    if len(encoded) <= budget:
        return text
    room = budget - len(suffix.encode("utf-8"))
    if room <= 0:
        return suffix.encode("utf-8")[:budget].decode("utf-8", "ignore")
    return encoded[:room].decode("utf-8", "ignore").rstrip() + suffix


def data_age_hours(data_dir: Path) -> float | None:
    """Age of the freshest snapshot in ``data_dir``, in hours, or None if unknown.

    Reads the pipeline's own ``generated_at`` rather than the file mtime: in CI the
    checkout sets every mtime to the clone time, which would always look fresh.
    """
    for name in ("latest-24h.json", "latest-24h-all.json"):
        path = data_dir / name
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        ts = payload.get("generated_at")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
    return None


def _is_stale(data_dir: Path, max_age: float) -> bool:
    """True when freshness cannot be established or exceeds the threshold.

    Missing/unreadable data counts as stale: absence of a freshness signal is a
    failure, not a pass. The opposite default would let a deleted or corrupt data
    file look healthy forever.
    """
    age = data_age_hours(data_dir)
    if age is None:
        return True
    return age > max_age


def build_ci_message(status: str, data_dir: Path, detail: str = "") -> str:
    icon = {"success": "✅", "cancelled": "⚠️", "timed_out": "⏱️"}.get(status, "🚨")
    lines = [
        f"# {icon} AI News Radar — Actions 异常",
        "",
        f"**状态**：`{status}`",
    ]
    workflow = os.environ.get("GITHUB_WORKFLOW")
    ref = os.environ.get("GITHUB_REF_NAME")
    if workflow:
        lines.append(f"**工作流**：{workflow}" + (f"（`{ref}`）" if ref else ""))
    if detail:
        lines.append(f"**说明**：{detail}")

    age = data_age_hours(data_dir)
    if age is not None:
        lines.append(f"**当前数据陈旧度**：{age:.1f} 小时")
    else:
        lines.append("**当前数据**：无法读取（数据文件缺失或损坏）")

    run_url = _run_url()
    if run_url:
        lines.append("")
        lines.append(f"[查看本次运行日志]({run_url})")
    return "\n".join(lines)


def build_stale_message(age: float | None, max_age: float, data_dir: Path, detail: str = "") -> str:
    age_text = f"{age:.1f} 小时" if age is not None else "未知（数据文件缺失或无法解析）"
    lines = [
        "# 🕒 AI News Radar — 数据长时间未更新",
        "",
        f"**数据陈旧度**：{age_text}",
        f"**告警阈值**：{max_age:.0f} 小时",
        "",
        "流水线可能仍在报成功，但发布的数据没有推进 —— 站点正在展示过期新闻。",
        "常见原因：全部信源抓取失败、运行时间不足、上游 API 静默降级。",
    ]
    if detail:
        lines.append(f"**补充**：{detail}")

    run_url = _run_url()
    if run_url:
        lines.append("")
        lines.append(f"[查看最近一次运行]({run_url})")
    return "\n".join(lines)


def send(markdown: str, webhook_url: str, webhook_type: str) -> bool:
    """POST the message. Returns True on a 2xx response; never raises."""
    webhook_type = (webhook_type or "markdown").strip().lower()
    if webhook_type in {"feishu", "lark"}:
        payload = {"msg_type": "text", "content": {"text": markdown}}
    elif webhook_type in {"slack", "generic", "text"}:
        # Slack 的 text 字段可直接放 markdown 纯文本，链接用 <url|label> 更佳，
        # 但纯 markdown 也能正常显示，保持与现有 notifier 一致以降低复杂度。
        payload = {"text": markdown}
    else:
        # wecom / wechat / dingtalk / markdown —— 企业微信默认走这条
        payload = {"msgtype": "markdown", "markdown": {"content": markdown}}

    try:
        resp = requests.post(webhook_url, json=payload, timeout=DEFAULT_TIMEOUT)
    except Exception as exc:  # 网络/超时/TLS 等：告警失败绝不能拖垮流水线
        print(f"[notify] 发送失败（网络异常）: {exc}", file=sys.stderr)
        return False

    if 200 <= resp.status_code < 300:
        # 企业微信成功时返回 {"errcode":0}，HTTP 200 也可能带非 0 errcode。
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if isinstance(body, dict) and body.get("errcode") not in (None, 0):
            print(
                f"[notify] 网关拒绝: errcode={body.get('errcode')} errmsg={body.get('errmsg')}",
                file=sys.stderr,
            )
            return False
        print("[notify] 已发送")
        return True

    print(f"[notify] 发送失败: HTTP {resp.status_code} {resp.text[:200]}", file=sys.stderr)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Send operational alerts for the radar pipeline.")
    ap.add_argument("--mode", choices=("ci", "stale"), required=True)
    ap.add_argument("--status", default="failure", help="CI 模式下的运行结论（failure/cancelled/timed_out）")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument(
        "--max-age-hours",
        type=float,
        default=6.0,
        help="stale 模式下的陈旧阈值（小时）",
    )
    ap.add_argument("--detail", default="", help="附加上下文（例如失败的步骤名）")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印消息，不实际发送",
    )
    ap.add_argument(
        "--fail-when-stale",
        action="store_true",
        help=(
            "stale 模式下，若数据确已过期则退出码 1（供独立看门狗把运行标红）。"
            "默认退出 0：嵌在更新流水线内的检查不应因数据陈旧而把一次成功运行判失败。"
        ),
    )
    args = ap.parse_args()

    data_dir = Path(args.data_dir)

    if args.mode == "ci":
        message = build_ci_message(args.status, data_dir, args.detail)
    else:
        age = data_age_hours(data_dir)
        message = build_stale_message(age, args.max_age_hours, data_dir, args.detail)

    message = _truncate_bytes(message)

    if args.dry_run:
        print(message)
        return 0

    webhook_url = os.environ.get("WEBHOOK_URL", "").strip()

    # 「是否陈旧」与「是否配置了 webhook」是两件事：前者决定退出码，后者只决定
    # 要不要发消息。未配置 webhook 时仍应让看门狗运行标红，否则告警能力缺失
    # 本身也会变成静默故障。
    stale = args.mode == "stale" and _is_stale(data_dir, args.max_age_hours)

    if not webhook_url:
        print("[notify] WEBHOOK_URL 未配置，跳过告警。")
        return 1 if (stale and args.fail_when_stale) else 0

    webhook_type = os.environ.get("WEBHOOK_TYPE", "markdown")
    # 交付失败不影响流水线结论：告警本身不该成为新的故障源。
    send(message, webhook_url, webhook_type)
    return 1 if (stale and args.fail_when_stale) else 0


if __name__ == "__main__":
    sys.exit(main())

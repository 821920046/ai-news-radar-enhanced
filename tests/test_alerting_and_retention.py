"""Regression tests for operational alerting and data-retention behaviour.

Covers the second round of hardening, driven by three concrete failures:

  1. Data retention — the archive prune condition ended in `or now`, so any record
     whose timestamps were all unusable evaluated as "just seen" and could never be
     evicted. The archive only ever grew (24986 stale records, oldest >200 days,
     frozen on the pipeline-state branch).

  2. Silence on failure — the workflow's only alert step used
     `if: failure() && env.WEBHOOK_URL != ''`. With no webhook configured the step
     was skipped entirely, and `failure()` does not match cancelled or timed_out
     runs, so the failure modes most likely to matter produced no signal at all.

  3. No staleness detection — a run could finish green while publishing days-old
     data (every source fetch failing, runner starved of time, upstream API
     degrading). Nothing compared the published timestamp against wall-clock time.

The tests deliberately use subprocess against the real scripts where practical, so
they exercise the same code path CI does rather than a reimplementation of it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NOTIFY = ROOT / "scripts" / "notify_ci.py"
WATCHDOG = ROOT / ".github" / "workflows" / "staleness-watchdog.yml"
WORKFLOW = ROOT / ".github" / "workflows" / "update-news.yml"


# ── 数据保留：归档裁剪必须真的能剔除陈旧记录 ──────────────────────────────


def _prune(records: dict, keep_days: int, now: datetime) -> dict:
    """Mirror of the pipeline's prune rule (core/pipeline/main_pipeline.py)."""
    from core.utils import parse_iso

    keep_after = now - timedelta(days=keep_days)
    out = {}
    for key, value in records.items():
        ts = (
            parse_iso(value.get("last_seen_at"))
            or parse_iso(value.get("published_at"))
            or parse_iso(value.get("first_seen_at"))
        )
        if ts is None:
            continue
        if ts >= keep_after:
            out[key] = value
    return out


def test_prune_drops_records_with_only_unusable_timestamps():
    """`or now` 兜底会让时间戳损坏的记录永生。必须剔除。"""
    now = datetime.now(timezone.utc)
    records = {
        "good_recent": {"last_seen_at": (now - timedelta(hours=1)).isoformat()},
        "good_old": {"last_seen_at": (now - timedelta(days=30)).isoformat()},
        "all_broken": {"last_seen_at": None, "published_at": "garbage", "first_seen_at": ""},
        "all_missing": {},
    }
    kept = _prune(records, keep_days=3, now=now)

    assert "good_recent" in kept
    assert "good_old" not in kept, "超过保留期的记录必须被剔除"
    assert "all_broken" not in kept, "时间戳全部损坏的记录必须被剔除（原 `or now` 会永久保留）"
    assert "all_missing" not in kept, "缺失全部时间戳的记录必须被剔除"


def test_prune_falls_back_across_timestamp_fields():
    """只要任一字段可用且新鲜，就应保留。"""
    now = datetime.now(timezone.utc)
    records = {
        "only_published_fresh": {
            "last_seen_at": None,
            "published_at": (now - timedelta(hours=2)).isoformat(),
        },
        "only_first_seen_fresh": {
            "last_seen_at": "",
            "first_seen_at": (now - timedelta(hours=5)).isoformat(),
        },
    }
    kept = _prune(records, keep_days=3, now=now)
    assert set(kept) == {"only_published_fresh", "only_first_seen_fresh"}


def test_pipeline_source_has_no_unconditional_now_fallback():
    """防止有人把 `or now` 兜底改回去。

    注意必须剥掉注释再判断：修复说明里本身就会提到 `or now` 这个词，
    直接对整段源码做子串匹配会把「解释这个 bug 的注释」误判成「这个 bug 还在」。
    """
    src = (ROOT / "core" / "pipeline" / "main_pipeline.py").read_text(encoding="utf-8")
    prune_start = src.find("# Prune archive")
    assert prune_start != -1, "未找到归档裁剪代码块"
    next_stage = src.find("# 24h window", prune_start)
    block = src[prune_start:next_stage if next_stage != -1 else prune_start + 3000]

    # 去掉整行注释，只留下真正会执行的代码
    code_lines = [
        line for line in block.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    code = "\n".join(code_lines)

    assert "or now" not in code, (
        "裁剪条件里不得再出现 `or now` 兜底：它让时间戳损坏的记录永生，归档只增不减"
    )
    assert "parse_iso" in code, "裁剪应基于可解析的时间戳"


# ── 陈旧数据检测 ──────────────────────────────────────────────────────────


def _write_snapshot(directory: Path, age_hours: float) -> None:
    ts = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    payload = {
        "generated_at": ts.isoformat().replace("+00:00", "Z"),
        "items_ai": [{"title": "t", "url": "https://e.com"}],
    }
    (directory / "latest-24h.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _run_notify(args: list[str], env_extra: dict | None = None):
    import os

    env = dict(os.environ)
    env.pop("WEBHOOK_URL", None)  # 默认不发真实请求
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, str(NOTIFY), *args],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=env,
    )


def test_fresh_data_is_not_flagged(tmp_path):
    _write_snapshot(tmp_path, age_hours=0.5)
    proc = _run_notify(
        ["--mode", "stale", "--data-dir", str(tmp_path), "--max-age-hours", "6", "--fail-when-stale"]
    )
    assert proc.returncode == 0, f"新鲜数据被误判为陈旧:\n{proc.stdout}{proc.stderr}"


def test_stale_data_is_flagged(tmp_path):
    _write_snapshot(tmp_path, age_hours=48)
    proc = _run_notify(
        ["--mode", "stale", "--data-dir", str(tmp_path), "--max-age-hours", "6", "--fail-when-stale"]
    )
    assert proc.returncode == 1, "陈旧数据必须让看门狗失败（这样才能被发现）"


def test_missing_data_counts_as_stale(tmp_path):
    """数据缺失不是「新鲜」，必须判为陈旧 —— 否则文件被删会永远显得健康。"""
    proc = _run_notify(
        ["--mode", "stale", "--data-dir", str(tmp_path), "--max-age-hours", "6", "--fail-when-stale"]
    )
    assert proc.returncode == 1, "数据文件缺失时应判为陈旧"


def test_stale_inside_pipeline_does_not_fail_successful_run(tmp_path):
    """嵌在更新流水线里的检查不应把一次成功运行判失败（默认不带 --fail-when-stale）。"""
    _write_snapshot(tmp_path, age_hours=48)
    proc = _run_notify(["--mode", "stale", "--data-dir", str(tmp_path), "--max-age-hours", "6"])
    assert proc.returncode == 0, "内嵌使用时陈旧不应导致退出 1"


def test_stale_survives_missing_webhook(tmp_path):
    """未配置 webhook 时，陈旧判定仍必须让小看门狗退出 1。

    否则「没配告警」会连带把「能发现故障」的能力一起关掉。
    """
    _write_snapshot(tmp_path, age_hours=100)
    proc = _run_notify(
        ["--mode", "stale", "--data-dir", str(tmp_path), "--fail-when-stale"]
    )
    assert proc.returncode == 1
    assert "WEBHOOK_URL" in proc.stdout


def test_corrupt_snapshot_is_treated_as_stale(tmp_path):
    (tmp_path / "latest-24h.json").write_text("{not json", encoding="utf-8")
    proc = _run_notify(
        ["--mode", "stale", "--data-dir", str(tmp_path), "--fail-when-stale"]
    )
    assert proc.returncode == 1, "损坏的数据文件必须判为陈旧"


# ── 告警闸门：只在「确属异常」时才推送 ──────────────────────────────────────
# 更新流水线每小时运行一次。若 stale 检查无条件发送，就等于每小时推一条
# 「数据长时间未更新」—— 而数据其实一直是新鲜的。噪音会训练人忽略这个频道，
# 真正的故障也就跟着被一起忽略。安静的一小时必须保持安静。


def _run_notify_main(monkeypatch, argv: list[str]) -> tuple[int, int]:
    """调用 notify_ci.main() 并返回 (退出码, requests.post 调用次数)。

    必须走 main() 而不是 send()：闸门逻辑在 main() 里，单独测 send() 永远
    发现不了「不该发却发了」这类问题。
    """
    sys.path.insert(0, str(ROOT))
    import scripts.notify_ci as n
    from unittest.mock import MagicMock, patch

    monkeypatch.setenv("WEBHOOK_URL", "https://example.invalid/hook")
    monkeypatch.setenv("WEBHOOK_TYPE", "wecom")
    monkeypatch.setattr(sys, "argv", ["notify_ci.py", *argv])

    resp = MagicMock(status_code=200, text="ok")
    resp.json.return_value = {"errcode": 0, "errmsg": "ok"}
    with patch.object(n.requests, "post", return_value=resp) as post:
        code = n.main()
    return code, post.call_count


def test_fresh_data_sends_no_alert(monkeypatch, tmp_path):
    """核心回归：数据新鲜时，即使配了 webhook 也**不能**推送。"""
    _write_snapshot(tmp_path, age_hours=0.5)
    code, calls = _run_notify_main(
        monkeypatch, ["--mode", "stale", "--data-dir", str(tmp_path), "--max-age-hours", "6"]
    )
    assert code == 0
    assert calls == 0, "数据新鲜却推送了告警 —— 这正是每小时一条噪音的来源"


def test_stale_data_does_send(monkeypatch, tmp_path):
    """数据确实陈旧时必须推送，否则告警通道形同虚设。"""
    _write_snapshot(tmp_path, age_hours=48)
    code, calls = _run_notify_main(
        monkeypatch, ["--mode", "stale", "--data-dir", str(tmp_path), "--max-age-hours", "6"]
    )
    assert calls == 1, "数据陈旧却不推送，告警失去意义"
    assert code == 0, "内嵌使用（不带 --fail-when-stale）不应把成功运行判失败"


def test_stale_watchdog_still_exits_nonzero(monkeypatch, tmp_path):
    """独立看门狗带 --fail-when-stale：既推送，也让运行标红。"""
    _write_snapshot(tmp_path, age_hours=48)
    code, calls = _run_notify_main(
        monkeypatch,
        [
            "--mode", "stale", "--data-dir", str(tmp_path),
            "--max-age-hours", "12", "--fail-when-stale",
        ],
    )
    assert calls == 1
    assert code == 1, "看门狗必须让运行失败才能被发现"


def test_ci_mode_sends_nothing_when_status_is_success(monkeypatch, tmp_path):
    """`--status success` 属于误用：不该变成一条噪音。"""
    _write_snapshot(tmp_path, age_hours=0.5)
    code, calls = _run_notify_main(
        monkeypatch, ["--mode", "ci", "--status", "success", "--data-dir", str(tmp_path)]
    )
    assert calls == 0
    assert code == 0


def test_ci_mode_sends_on_every_abnormal_status(monkeypatch, tmp_path):
    """failure / cancelled / timed_out —— 这些才是该推送的异常。"""
    _write_snapshot(tmp_path, age_hours=0.5)
    for status in ("failure", "cancelled", "timed_out"):
        _code, calls = _run_notify_main(
            monkeypatch, ["--mode", "ci", "--status", status, "--data-dir", str(tmp_path)]
        )
        assert calls == 1, f"{status} 属于异常，必须推送"


def test_trend_history_prune_drops_unusable_dates():
    """趋势历史里日期不可解析的条目同样必须剔除。

    早期实现是「保守保留」，但损坏条目会永久占据 max_history_entries 配额，
    把真正可用的历史挤出去，导致突发检测基线失真。
    """
    from core.trend_engine.trend_detector import TrendDetector

    detector = TrendDetector({"max_history_days": 7, "max_history_entries": 50})
    now = datetime.now(timezone.utc)
    entries = [
        {"date": now.isoformat(), "clusters": []},
        {"date": (now - timedelta(days=30)).isoformat(), "clusters": []},
        {"date": None, "clusters": []},
        {"date": "garbage", "clusters": []},
        {},
    ]
    kept = detector._prune_history(entries)

    assert len(kept) == 1, f"应只保留 1 条新鲜记录，实际 {len(kept)}"
    assert kept[0]["date"] == now.isoformat()


def test_trend_history_respects_entry_cap():
    from core.trend_engine.trend_detector import TrendDetector

    detector = TrendDetector({"max_history_days": 7, "max_history_entries": 3})
    now = datetime.now(timezone.utc)
    entries = [{"date": (now - timedelta(hours=i)).isoformat(), "clusters": []} for i in range(10)]
    kept = detector._prune_history(entries)
    assert len(kept) == 3, "超过硬上限时必须截断"


# ── 告警消息与投递 ────────────────────────────────────────────────────────


def test_ci_message_mentions_status_and_run_url(tmp_path, monkeypatch):
    _write_snapshot(tmp_path, age_hours=1)
    import os

    env = dict(os.environ)
    env.update(
        {
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_RUN_ID": "12345",
            "GITHUB_SERVER_URL": "https://github.com",
        }
    )
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")

    sys.path.insert(0, str(ROOT))
    import scripts.notify_ci as n

    msg = n.build_ci_message("failure", tmp_path, "步骤：Update data")
    assert "failure" in msg
    assert "actions/runs/12345" in msg, "应带上运行链接便于排查"
    assert "步骤：Update data" in msg


def test_wecom_payload_shape(monkeypatch):
    """企业微信要求 {"msgtype":"markdown","markdown":{"content":...}}。"""
    sys.path.insert(0, str(ROOT))
    import scripts.notify_ci as n
    from unittest.mock import MagicMock, patch

    with patch.object(n.requests, "post") as post:
        resp = MagicMock(status_code=200, text="ok")
        resp.json.return_value = {"errcode": 0, "errmsg": "ok"}
        post.return_value = resp
        ok = n.send("hello", "https://example.invalid/hook", "wecom")

    assert ok is True
    assert post.call_args.kwargs["json"] == {
        "msgtype": "markdown",
        "markdown": {"content": "hello"},
    }


def test_wecom_business_error_is_a_failure(monkeypatch):
    """HTTP 200 但 errcode 非 0，属于投递失败，不能算成功。"""
    sys.path.insert(0, str(ROOT))
    import scripts.notify_ci as n
    from unittest.mock import MagicMock, patch

    with patch.object(n.requests, "post") as post:
        resp = MagicMock(status_code=200, text="bad")
        resp.json.return_value = {"errcode": 93000, "errmsg": "invalid webhook url"}
        post.return_value = resp
        ok = n.send("hello", "https://example.invalid/hook", "wecom")

    assert ok is False, "errcode != 0 必须判为投递失败"


def test_delivery_never_raises(monkeypatch):
    """告警失败绝不能把一次成功的流水线变成失败。"""
    sys.path.insert(0, str(ROOT))
    import scripts.notify_ci as n
    from unittest.mock import patch

    for exc in (Exception("boom"), OSError("net down"), ValueError("weird")):
        with patch.object(n.requests, "post", side_effect=exc):
            assert n.send("x", "https://example.invalid/hook", "wecom") is False


def test_message_is_byte_truncated_for_wecom_budget():
    """企业微信 markdown 上限约 4096 字节，超长必须截断而不是被网关拒绝。"""
    sys.path.insert(0, str(ROOT))
    import scripts.notify_ci as n

    huge = "中" * 5000  # 每字 3 字节 => 15000 字节
    out = n._truncate_bytes(huge)
    assert len(out.encode("utf-8")) <= n.MESSAGE_BYTE_BUDGET
    assert "截断" in out


# ── 工作流接线 ────────────────────────────────────────────────────────────


def test_standalone_watchdog_workflow_exists_and_is_scheduled():
    """独立看门狗：更新流水线本身没跑起来时，它是唯一的发现手段。"""
    assert WATCHDOG.exists(), "缺少独立的数据陈旧看门狗工作流"
    text = WATCHDOG.read_text(encoding="utf-8")
    assert "schedule:" in text and "cron:" in text, "看门狗必须有自己的调度"
    assert "notify_ci.py" in text, "看门狗必须调用告警脚本"
    assert "--fail-when-stale" in text, "看门狗必须在数据陈旧时让运行失败"


def test_main_workflow_alerts_on_any_non_success():
    """`if: failure()` 匹配不到 cancelled / timed_out，必须用 !success()。"""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "!success()" in text, "失败告警必须覆盖 cancelled / timed_out"
    assert "if: failure() && env.WEBHOOK_URL" not in text, (
        "不得用 `failure() && WEBHOOK_URL != ''`：未配置 webhook 时整个告警步骤会被跳过"
    )


def test_main_workflow_has_staleness_watchdog_step():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "notify_ci.py" in text, "更新流水线内也应检查数据陈旧"
    assert "--mode stale" in text


def test_pipeline_state_is_actually_read_and_written():
    """注释曾声称归档由 pipeline-state 维护，但没有任何代码读写它 —— 归档因此丢失。"""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "Restore dedupe archive" in text, "缺少归档恢复步骤"
    assert "Persist dedupe archive" in text, "缺少归档保存步骤"
    assert "git show FETCH_HEAD:data/archive.json" in text, "恢复必须真的取归档内容"
    assert "refs/heads/pipeline-state" in text, "保存必须真的推回该分支"


def test_archive_is_never_staged_into_main():
    """archive.json 13.8MB，一旦进入 main 历史就会永久膨胀仓库。"""
    text = WORKFLOW.read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("git add "):
            continue
        # /tmp/pipeline-state 下的 add 是允许的（那是派生分支，不是 main）
        if "pipeline-state" in stripped or "-f data/archive.json" in stripped:
            continue
        assert "data/archive.json" not in stripped, (
            f"不得把 archive.json add 进 main：{stripped}"
        )

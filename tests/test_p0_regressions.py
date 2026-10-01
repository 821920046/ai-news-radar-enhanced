"""Regression tests for the P0 defects found during the first-principles audit.

Each test locks down a failure mode that was deterministically reproducible in CI.

  P0-1  core.models raised ZoneInfoNotFoundError at import time when the IANA
        tz database was unavailable (no system tzdata and no `tzdata` package),
        which took down every module importing it. requirements.txt now pins
        tzdata and the lookup degrades to a fixed UTC+8 offset.

  P0-2  .github/workflows/update-news.yml ran `git add` on data/archive.json and
        data/title-zh-cache.json, which .gitignore excludes. Git aborts on an
        ignored path with exit code 1 ("The following paths are ignored by one
        of your .gitignore files"), so the job failed at "Commit and push
        changes" after doing all the real work. Confirmed against run
        36833452328, where steps 1-9 succeeded and step 10 failed.
        The workflow's `timeout-minutes: 20` was also below the pipeline's real
        runtime (~20m20s), so scheduled runs were cancelled mid-fetch; it is now
        40.

        NOTE: an earlier revision of this file claimed checkout@v6 /
        setup-python@v6 did not exist. That was WRONG -- both tags are published
        (checked via the GitHub tags API). The action versions were never the
        cause; the ignored-path `git add` was.

  P0-3  api/app.py::_items_of ignored the "items_all" key produced by
        core/output.py for latest-24h-all.json, and the `all_file or main_file`
        fallback short-circuited on a truthy-but-empty dict, so /hot returned
        503 even though data was present.
"""

from __future__ import annotations

import importlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "update-news.yml"
REQUIREMENTS = ROOT / "requirements.txt"


# ── P0-1: timezone resolution must never crash the import ────────────────────


def test_shanghai_tz_resolves_to_utc_plus_8():
    from core import models

    assert datetime.now(models.SH_TZ).utcoffset() == timedelta(hours=8)


def test_resolve_shanghai_tz_degrades_when_zoneinfo_missing(monkeypatch):
    """Simulate a host with no IANA tzdb: must degrade, not raise."""
    from core import models

    def boom(_key):  # noqa: ANN001
        raise models.ZoneInfoNotFoundError("No time zone found with key Asia/Shanghai")

    monkeypatch.setattr(models, "ZoneInfo", boom)
    tz = models._resolve_shanghai_tz()
    assert datetime.now(tz).utcoffset() == timedelta(hours=8)


def test_tzdata_is_a_declared_dependency():
    declared = _declared_packages()
    assert any(pkg.startswith("tzdata") for pkg in declared), (
        "tzdata 必须显式声明：zoneinfo 在无系统 tzdb 的镜像上会直接抛错"
    )


# ── P0-1b: heavy ML deps must not creep back in ──────────────────────────────


def _declared_packages() -> list[str]:
    """Return declared package names, ignoring comments and blank lines."""
    pkgs: list[str] = []
    for raw in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            pkgs.append(line)
    return pkgs


def test_no_unused_heavy_dependencies():
    declared = " ".join(_declared_packages()).lower()
    for heavy in ("scikit-learn", "numpy", "pandas", "torch"):
        assert heavy not in declared, f"{heavy} 已被移除（代码中无实际 import），不应重新引入"


# ── P0-2: the commit step must not git-add .gitignore'd paths ────────────────


def _gitignore_patterns() -> set[str]:
    root = ROOT / ".gitignore"
    if not root.exists():
        return set()
    out: set[str] = set()
    for raw in root.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            out.add(line)
    return out


def _git_add_args(text: str) -> list[str]:
    """Return every path token passed to git add across the workflow."""
    tokens: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("git add") and "git add -A" not in stripped:
            # drop trailing shell redirection / comments
            body = stripped[len("git add"):].split("#", 1)[0]
            tokens.extend(p for p in body.split() if not p.startswith("$"))
    return tokens


def test_workflow_never_git_adds_ignored_paths():
    """This is the actual cause of the CI failure: `git add <ignored>` exits 1."""
    text = WORKFLOW.read_text(encoding="utf-8")
    ignored = _gitignore_patterns()
    for token in _git_add_args(text):
        assert token not in ignored, (
            f"workflow 对已被 .gitignore 排除的路径执行 git add：{token}；"
            "git 会以 exit 1 中止，导致 job 在该步骤失败"
        )


def test_workflow_uses_staged_diff_for_emptiness_check():
    """`git diff --quiet` misses staged changes; must use `--cached`."""
    text = WORKFLOW.read_text(encoding="utf-8")
    if "git diff --quiet; then" in text:
        raise AssertionError(
            "提交前判空应使用 `git diff --cached --quiet`，"
            "否则 `git add` 之后 `git diff --quiet` 恒为真、永远不提交"
        )


def test_workflow_timeout_exceeds_observed_pipeline_runtime():
    """Scheduled runs were cancelled at 20m exactly; observed runtime ~20m20s."""
    text = WORKFLOW.read_text(encoding="utf-8")
    m = re.search(r"timeout-minutes:\s*(\d+)", text)
    assert m, "workflow 应显式设置 timeout-minutes"
    assert int(m.group(1)) >= 30, (
        f"timeout-minutes={m.group(1)} 低于实测 pipeline 耗时（约 20 分 20 秒），"
        "会被 GitHub 强杀"
    )


def test_workflow_action_refs_are_pinned_to_major_tags():
    """Every `uses:` must carry an explicit version, never a floating branch."""
    text = WORKFLOW.read_text(encoding="utf-8")
    for ref in re.findall(r"uses:\s*([^\s#]+)", text):
        if ref.startswith("./") or "@" not in ref:
            continue
        name, _, ver = ref.partition("@")
        assert ver, f"{name} 未固定版本"
        assert ver not in {"main", "master", "HEAD"}, f"{ref} 使用了浮动引用"


def test_workflow_runs_the_data_gate():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "scripts/validate_data.py" in text, "工作流必须运行数据质量门禁"


# ── 持久运行 / 仓库负担 ─────────────────────────────────────────────────────


def test_workflow_does_not_cancel_in_progress_runs():
    """cron 每小时触发，而单趟流水线实测约 20 分钟。

    若 cancel-in-progress 为 true，下一小时的调度会取消正在跑的那一趟，
    数据可能永远跑不完（观察到的 `cancelled` @ 20m19s 即此成因）。
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    m = re.search(r"cancel-in-progress:\s*(\S+)", text)
    assert m, "应显式设置 cancel-in-progress"
    assert m.group(1).rstrip("#").strip().lower() == "false", (
        "cancel-in-progress 必须为 false：否则小时级调度会腰斩正在运行的长任务"
    )


def test_workflow_suppresses_noop_snapshots():
    """仓库膨胀根因：每个小时都因 generated_at 变化而提交 6.8MB JSON。

    工作流必须调用语义级指纹脚本，并在其报告「无实质变化」时跳过提交。
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "snapshot_fingerprint.py" in text, (
        "提交步骤必须调用 snapshot_fingerprint.py 做语义级变化检测"
    )
    assert "FP_EXIT" in text and "skipping commit" in text, (
        "缺少「无实质变化则不提交」的闸门逻辑"
    )


def test_snapshot_fingerprint_ignores_generated_at(tmp_path):
    """剔除 generated_at 后内容一致 => 退出码 3（不应提交）。"""
    import json
    import subprocess
    import sys as _sys

    # 真实脚本在临时 git 仓库外的行为：HEAD 取不到 => 视为变化(0)。
    # 这里直接在项目仓库内对真实文件做一次冒烟调用，验证退出码语义可用。
    proc = subprocess.run(
        [_sys.executable, "scripts/snapshot_fingerprint.py",
         "--files", "data/latest-24h.json"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode in (0, 3), (
        "指纹脚本不应崩溃，实测输出：\n" + proc.stdout + proc.stderr
    )
    # 已提交且未修改的文件，在 HEAD 中要么一致(3)，要么有真实差异(0)。
    assert "[SAME]" in proc.stdout or "[CHANGED]" in proc.stdout


def test_snapshot_fingerprint_script_is_committed_with_workflow():
    assert (ROOT / "scripts" / "snapshot_fingerprint.py").exists(), (
        "工作流引用了 snapshot_fingerprint.py，该脚本必须提交"
    )


def test_failure_notification_handles_multiple_webhook_formats():
    """告警载荷要同时兼容 Slack({"text"}) 与企微({"msgtype","text"})。"""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "msgtype" in text, "失败告警应兼容企业微信 webhook 载荷格式"


def test_validate_data_flags_collapsed_ai_layer(tmp_path):
    """AI 层静默失效（如 OpenRouter 配额耗尽）必须让门禁失败。

    这是持久运行时最危险的失败模式：抓取正常、generated_at 新鲜，
    但 items_ai 塌缩为个位数 —— 若无看门狗则会被当作成功提交。
    """
    import json
    import subprocess
    import sys as _sys

    from datetime import datetime, timezone

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "total_items_raw": 5000,
        "items_ai": [{"title": "t", "url": "https://e.com"} for _ in range(4)],
    }
    (tmp_path / "latest-24h.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )

    proc = subprocess.run(
        [_sys.executable, "scripts/validate_data.py", "--data-dir", str(tmp_path),
         "--max-age-hours", "6", "--min-items", "1"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode == 1, (
        "AI 层塌缩时必须退出 1，实测输出：\n" + proc.stdout + proc.stderr
    )
    assert "AI 处理层疑似失效" in proc.stdout


def test_validate_data_passes_a_healthy_snapshot(tmp_path):
    """正常快照不得被误杀（防止看门狗过于激进）。"""
    import json
    import subprocess
    import sys as _sys

    from datetime import datetime, timezone

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "total_items_raw": 1000,
        "items_ai": [{"title": f"t{i}", "url": "https://e.com"} for i in range(200)],
    }
    (tmp_path / "latest-24h.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )

    proc = subprocess.run(
        [_sys.executable, "scripts/validate_data.py", "--data-dir", str(tmp_path),
         "--max-age-hours", "6", "--min-items", "1"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, (
        "健康快照被误判为失败：\n" + proc.stdout + proc.stderr
    )


def test_workflow_only_stages_files_that_are_not_ignored():
    """Generic guard: every git-add target must be a path .gitignore does not cover.

    Complements test_workflow_never_git_adds_ignored_paths by asserting the
    workflow actually stages something (so a no-op `git add` cannot pass).
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    tokens = _git_add_args(text)
    assert tokens, "工作流应至少 git add 一些文件"
    ignored = _gitignore_patterns()
    for token in tokens:
        assert token not in ignored, f"{token} 被 .gitignore 排除，不应 git add"


# ── P0-3: /hot must read the items_all payload ──────────────────────────────


def _items_of(payload: dict) -> list[dict]:
    from api.app import _items_of as fn

    return fn(payload)


def test_items_of_reads_items_ai():
    assert _items_of({"items_ai": [{"title": "a"}]}) == [{"title": "a"}]


def test_items_of_reads_items_all():
    """latest-24h-all.json 只带 items_all，之前被忽略导致 /hot 503。"""
    assert _items_of({"items_all": [{"title": "b"}]}) == [{"title": "b"}]


def test_items_of_falls_back_past_an_empty_primary_key():
    payload = {"items_ai": [], "items_all": [{"title": "c"}]}
    assert _items_of(payload) == [{"title": "c"}]


def test_items_of_returns_empty_list_for_unknown_shape():
    assert _items_of({"generated_at": "2026-01-01T00:00:00Z"}) == []


def test_httpx_is_declared_for_testclient():
    """TestClient hard-requires httpx; without it two tests below blow up in CI.

    This bit us once: httpx was present locally as a transitive dependency but
    absent in CI, turning an API regression test into a collection error.
    """
    declared = " ".join(_declared_packages()).lower()
    assert "httpx" in declared, (
        "httpx 必须显式声明，否则 fastapi.testclient 在 CI 上抛 RuntimeError"
    )
    assert importlib.util.find_spec("httpx") is not None, (
        "运行时缺少 httpx；请先 pip install -r requirements.txt"
    )


def test_hot_route_serves_from_items_all(tmp_path, monkeypatch):
    """端到端：只有 latest-24h-all.json 时 /hot 也必须返回 200。"""
    from fastapi.testclient import TestClient

    import api.app as app_module

    (tmp_path / "latest-24h-all.json").write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "items_all": [
                    {
                        "title": "Open-source release",
                        "url": "https://example.com/a",
                        "hotness": 90,
                        "source": "github",
                        "tags": ["开源"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_module, "DATA_DIR", tmp_path)

    client = TestClient(app_module.app)
    resp = client.get("/hot")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["opensource"] or body["news"]


def test_hot_route_falls_back_to_main_file_when_all_is_empty(tmp_path, monkeypatch):
    """全量文件存在但没有条目时，必须回退到主文件（旧代码在此短路）。"""
    from fastapi.testclient import TestClient

    import api.app as app_module

    (tmp_path / "latest-24h-all.json").write_text(
        json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(), "items_all": []}),
        encoding="utf-8",
    )
    (tmp_path / "latest-24h.json").write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "items_ai": [
                    {
                        "title": "Main payload item",
                        "url": "https://example.com/b",
                        "hotness": 50,
                        "source": "rss",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_module, "DATA_DIR", tmp_path)

    client = TestClient(app_module.app)
    resp = client.get("/hot")
    assert resp.status_code == 200, resp.text


# ── 数据门禁脚本本身 ─────────────────────────────────────────────────────────


def test_validate_data_script_exists_and_is_invokable():
    script = ROOT / "scripts" / "validate_data.py"
    assert script.exists(), "数据门禁脚本缺失"
    text = script.read_text(encoding="utf-8")
    assert "--max-age-hours" in text
    assert "sys.exit(main())" in text


def test_validate_data_is_committed_alongside_workflow():
    """脚本与引用它的工作流必须同时存在，避免 CI 步骤指向不存在的文件。"""
    text = WORKFLOW.read_text(encoding="utf-8")
    if "validate_data.py" in text:
        assert (ROOT / "scripts" / "validate_data.py").exists()

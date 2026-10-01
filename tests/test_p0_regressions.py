"""Regression tests for the three P0 defects found during the first-principles audit.

Each test locks down a failure mode that was deterministically reproducible:

  P0-1  core.models raised ZoneInfoNotFoundError at import time when the IANA
        tz database was unavailable (no system tzdata and no `tzdata` package),
        which took down every module importing it. requirements.txt now pins
        tzdata and the lookup degrades to a fixed UTC+8 offset.

  P0-2  .github/workflows/update-news.yml referenced actions/checkout@v6 and
        actions/setup-python@v6, which do not exist (v5/v6 were never released),
        so the job failed on its very first step. The workflow must only
        reference published major versions and must run the data gate.

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


# ── P0-2: GitHub Actions must reference published versions ───────────────────


def test_workflow_uses_only_published_action_versions():
    text = WORKFLOW.read_text(encoding="utf-8")
    uses = re.findall(r"uses:\s*([^\s#]+)", text)

    known = {
        "actions/checkout": {"v4"},
        "actions/setup-python": {"v5"},
        "actions/upload-artifact": {"v4"},
        "actions/download-artifact": {"v4"},
        "actions/configure-pages": {"v5"},
        "actions/upload-pages-artifact": {"v3"},
        "actions/deploy-pages": {"v4"},
    }
    for ref in uses:
        if "@" not in ref:
            continue
        name, _, ver = ref.partition("@")
        if name in known:
            assert ver in known[name], (
                f"{ref} 引用了未发布的版本；{name} 的可用大版本为 {sorted(known[name])}"
            )


def test_workflow_has_no_v6_or_v7_action_refs():
    text = WORKFLOW.read_text(encoding="utf-8")
    bad = re.findall(r"uses:\s*(actions/[^\s@]+@v[67])", text)
    assert not bad, f"发现不存在的 action 版本: {bad}"


def test_workflow_runs_the_data_gate():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "scripts/validate_data.py" in text, "工作流必须运行数据质量门禁"


def test_workflow_does_not_stage_gitignored_artifacts():
    text = WORKFLOW.read_text(encoding="utf-8")
    add_lines = [ln for ln in text.splitlines() if "git add" in ln]
    assert add_lines, "工作流应包含 git add 步骤"
    for ln in add_lines:
        assert "archive.json" not in ln, "archive.json 已被 gitignore，不应加入 git add"
        assert "title-zh-cache.json" not in ln, (
            "title-zh-cache.json 已被 gitignore，不应加入 git add"
        )


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

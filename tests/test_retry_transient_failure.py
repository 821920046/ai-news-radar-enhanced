"""回归测试：retry-transient-failures 工作流 + retry_transient_failure.sh。

本文件补上了一个此前完全缺失的测试面 —— `tests/mock_gh_retry.sh` 早就存在，
但没有任何测试使用它。缺失的代价是真实发生过的：

  该工作流的 job 级 if 只受理 failure / startup_failure / timed_out，并明确
  把 cancelled 当作「人的决定」拒绝。但 GitHub 对「job 超出 timeout-minutes」
  给的就是 cancelled（实测本仓库 0 个 timed_out、271 个 cancelled，且每个
  cancelled 都恰好停在超时线上）。于是它对唯一真实发生的故障类型完全失明，
  1964 次运行里**一次重试都没做过**（全仓库 attempt>=2 的运行数为 0）。

另外它还同时监听 3 个**链式**工作流（Update → Deploy → Cleanup），而
workflow_run 没有按 conclusion 过滤的能力，导致每个小时周期产生 3 次运行，
88% 是空跑。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "retry-transient-failures.yml"
SCRIPT = REPO_ROOT / "tools" / "ci" / "retry_transient_failure.sh"
MOCK = REPO_ROOT / "tests" / "mock_gh_retry.sh"

# 与 update-news.yml 中声明的一致：job 级 40 分钟，步骤级 20 分钟。
UPDATE_NEWS_WORKFLOW_YAML = """
name: Update AI News Snapshot
jobs:
  update:
    runs-on: ubuntu-latest
    timeout-minutes: 40
    steps:
      - name: Update data
        timeout-minutes: 20
        run: echo hi
"""


def _bash() -> str:
    """返回一个可用的 POSIX bash。

    Windows 上不能直接用 "bash"：PATH 里的 C:\\Windows\\System32\\bash.exe 是
    WSL 的转发器，在受限环境里会被安全策略拦掉，而它吐的是 UTF-16 垃圾而不是
    真实的断言信息。优先用 Git for Windows 自带的 bash。
    """
    override = os.environ.get("RETRY_TEST_BASH")
    if override and Path(override).exists():
        return override
    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
    ):
        if Path(candidate).exists():
            return candidate
    return "bash"


BASH = _bash()


def _pypi_jq_available() -> bool:
    try:
        import jq  # noqa: F401
    except ImportError:
        return False
    return True


# 决策脚本用 jq 解析 gh 的输出。jq 在 GitHub 托管 runner 上是预装的，所以 CI
# 一定有；本机若两者都没有，就明确 skip 而不是给出一堆看不懂的失败。
HAS_JQ = bool(shutil.which("jq")) or _pypi_jq_available()
needs_jq = pytest.mark.skipif(
    not HAS_JQ,
    reason="需要 jq（系统 jq，或 `pip install jq`）才能执行决策脚本",
)


def _jq_shim_dir(base: Path) -> Path | None:
    """系统没有 jq 时，造一个指向 tests/jq_cli.py 的 `jq` 替身。

    jq 在 GitHub 托管 runner 上是预装的，脚本依赖它是对的。本机通常没有，
    而下载静态二进制常被代理挡住 —— 于是用 PyPI `jq` 包（真 libjq 绑定）
    顶上，保证本地也能跑出真实结论，而不是一律 skip。
    """
    if shutil.which("jq") or not _pypi_jq_available():
        return None

    shim_dir = base / "jqshim"
    shim_dir.mkdir(parents=True, exist_ok=True)
    shim = shim_dir / "jq"
    # 用绝对路径的 python，避免依赖 PATH 里的解释器。
    # 路径必须转成 POSIX 形式：Windows 的反斜杠会被 shell 当作转义符吃掉。
    py = Path(sys.executable).as_posix()
    cli = (Path(__file__).resolve().parent / "jq_cli.py").as_posix()
    shim.write_text(f'#!/bin/sh\nexec "{py}" "{cli}" "$@"\n', encoding="utf-8")
    shim.chmod(0o755)
    return shim_dir


def _msys_path(path: Path) -> str:
    """把路径转成 Git Bash 认得的 POSIX 形式。

    ⚠️ 踩过：直接把 Windows 路径（`C:\\Users\\...`）塞进 PATH，Git Bash 解析不了
    （反斜杠在 POSIX 里只是普通字符），于是 `gh` 悄悄落回**系统里真正的 gh**，
    测试实际打了真实 GitHub API 还浑然不觉。必须转成 `/c/Users/...`。
    """
    posix = path.as_posix()
    if len(posix) > 1 and posix[1] == ":":
        return "/" + posix[0].lower() + posix[2:]
    return posix


_SHIMS: tuple[str, str | None] | None = None


def _shims() -> tuple[str, str | None]:
    """gh / jq 替身每个进程只建一次。

    用 tempfile 而不是 pytest 的 tmp_path：每个测试都新建目录会让 pytest 的
    清理阶段触发宿主环境的批量删除保护，把整个进程 SIGTERM 掉（那是环境噪音，
    不是测试失败）。只建一次既避开它，也更快。
    """
    global _SHIMS
    if _SHIMS is None:
        base = Path(tempfile.mkdtemp(prefix="retry_shims_"))
        bindir = base / "bin"
        bindir.mkdir(parents=True, exist_ok=True)
        gh = bindir / "gh"
        shutil.copyfile(MOCK, gh)
        gh.chmod(0o755)
        jq_dir = _jq_shim_dir(base)
        _SHIMS = (
            _msys_path(bindir),
            _msys_path(jq_dir) if jq_dir is not None else None,
        )
    return _SHIMS


def _run_script(tmp_path: Path, **env_overrides) -> subprocess.CompletedProcess:
    """在 mock 掉 gh 的环境里执行决策脚本。"""
    bindir, jq_dir = _shims()

    env = dict(os.environ)
    env.update(
        {
            "REPO": "owner/name",
            "RUN_ID": "12345",
            "DEFAULT_BRANCH": "main",
            "MOCK_WORKFLOW_CONTENT": UPDATE_NEWS_WORKFLOW_YAML,
            "MOCK_RERUN_LOG": str(tmp_path / "rerun.log"),
        }
    )
    env.update({k: str(v) for k, v in env_overrides.items()})

    parts = [bindir]
    if jq_dir:
        parts.append(jq_dir)
    parts.append(env.get("PATH", ""))
    env["PATH"] = ":".join(parts)

    return subprocess.run(
        [BASH, str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        # Windows 上 gh/jq 的 stderr 可能混入本地编码字节；不 replace 会抛
        # UnicodeDecodeError 而不是给出有用的断言信息。
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def _rerun_requested(tmp_path: Path) -> bool:
    log = tmp_path / "rerun.log"
    return log.exists() and "rerun requested" in log.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 工作流配置
# ---------------------------------------------------------------------------


class TestWorkflowTriggerSurface:
    """监听面收敛：避免 Update→Deploy→Cleanup 链式扇入造成的空跑洪水。"""

    @pytest.fixture(scope="class")
    def workflow(self) -> str:
        return WORKFLOW.read_text(encoding="utf-8")

    def test_watches_only_the_critical_workflow(self, workflow: str):
        block = workflow.split("workflow_run:", 1)[1].split("permissions:", 1)[0]
        watched = re.findall(r'^\s*-\s*"([^"]+)"', block, flags=re.MULTILINE)
        assert watched == ["Update AI News Snapshot"], (
            f"监听面应只保留关键路径，实际={watched}。"
            "多监听一个链式工作流就会多一整轮空跑（workflow_run 无法按 conclusion 过滤）"
        )

    def test_does_not_watch_its_own_chain_downstream(self, workflow: str):
        """Deploy/Cleanup 是被 Update 触发的下游，监听它们必然产生级联。"""
        block = workflow.split("workflow_run:", 1)[1].split("permissions:", 1)[0]
        for downstream in ("Deploy to GitHub Pages", "Cleanup Actions Artifacts"):
            assert downstream not in block, (
                f"仍在监听下游工作流「{downstream}」；它与 Update 构成链式级联，"
                "每个小时周期会多产生一次空跑"
            )

    def test_cancelled_runs_are_admitted_to_the_script(self, workflow: str):
        """cancelled 必须放行到脚本 —— 超时 kill 也被报成 cancelled。"""
        block = workflow.split("if: >-", 1)[1].split("runs-on:", 1)[0]
        assert "cancelled" in block, (
            "job 级 if 把 cancelled 挡在门外，而 GitHub 对 job 超时给的就是 cancelled，"
            "等于对唯一真实发生的故障类型失明"
        )
        for conclusion in ("failure", "startup_failure", "timed_out"):
            assert conclusion in block, f"if 条件丢失了 {conclusion}"

    def test_passes_default_branch_and_timeout_cap(self, workflow: str):
        """判定超时需要默认分支上的工作流文件，以及独立的重试上限。"""
        assert "DEFAULT_BRANCH" in workflow, "未把默认分支传给脚本，读超时会读到特性分支"
        assert "MAX_TIMEOUT_ATTEMPTS" in workflow, "缺少超时 kill 的独立重试上限"


# ---------------------------------------------------------------------------
# 脚本决策
# ---------------------------------------------------------------------------


@needs_jq
class TestTimeoutKillIsRecognised:
    """核心回归：被超时 kill 的 cancelled 运行必须被识别并重试。"""

    def test_cancelled_at_the_timeout_is_retried(self, tmp_path: Path):
        # 40 分钟预算，实际跑了 40m22s（与真实事故 run 36890199782 一致）
        res = _run_script(
            tmp_path,
            CONCLUSION="cancelled",
            RUN_ATTEMPT="1",
            WORKFLOW_NAME="Update AI News Snapshot",
            MOCK_RUN_SECONDS=str(40 * 60 + 22),
        )
        assert _rerun_requested(tmp_path), (
            f"被超时 kill 的运行没有触发重试。\nstdout={res.stdout}\nstderr={res.stderr}"
        )
        assert "timeout-kill" in res.stderr

    def test_cancelled_early_is_a_human_decision(self, tmp_path: Path):
        # 只跑了 3 分钟就取消 —— 人的决定，绝不能复活
        res = _run_script(
            tmp_path,
            CONCLUSION="cancelled",
            RUN_ATTEMPT="1",
            WORKFLOW_NAME="Update AI News Snapshot",
            MOCK_RUN_SECONDS=str(3 * 60),
        )
        assert not _rerun_requested(tmp_path), "把人的取消当成了超时 kill 并重试"
        assert "human decision" in res.stderr

    def test_tolerance_boundary(self, tmp_path: Path):
        """阈值 80%：40 分钟预算下，31 分钟（77.5%）算人的决定，33 分钟（82.5%）算超时。"""
        below = _run_script(
            tmp_path,
            CONCLUSION="cancelled",
            RUN_ATTEMPT="1",
            WORKFLOW_NAME="Update AI News Snapshot",
            MOCK_RUN_SECONDS=str(31 * 60),
        )
        assert not _rerun_requested(tmp_path), f"低于阈值却重试了: {below.stderr}"

        above = _run_script(
            tmp_path,
            CONCLUSION="cancelled",
            RUN_ATTEMPT="1",
            WORKFLOW_NAME="Update AI News Snapshot",
            MOCK_RUN_SECONDS=str(33 * 60),
        )
        assert _rerun_requested(tmp_path), f"高于阈值却没重试: {above.stderr}"

    def test_repeated_timeout_is_not_retried_forever(self, tmp_path: Path):
        """连续两次同样的超时是确定性缺陷，第三次重试只会再烧 40 分钟。"""
        res = _run_script(
            tmp_path,
            CONCLUSION="cancelled",
            RUN_ATTEMPT="2",  # MAX_TIMEOUT_ATTEMPTS=2
            WORKFLOW_NAME="Update AI News Snapshot",
            MOCK_RUN_SECONDS=str(40 * 60 + 22),
        )
        assert not _rerun_requested(tmp_path), "重复超时仍被无限重试"
        assert "deterministic defect" in res.stderr

    def test_missing_metadata_refuses_to_guess(self, tmp_path: Path):
        """读不到运行元数据时必须拒绝猜测，而不是默认重试或默认跳过。"""
        res = _run_script(
            tmp_path,
            CONCLUSION="cancelled",
            RUN_ATTEMPT="1",
            WORKFLOW_NAME="Update AI News Snapshot",
            MOCK_RUN_META_MISSING="1",
        )
        assert not _rerun_requested(tmp_path), "元数据缺失时仍然发起了重试"
        assert "human decision" in res.stderr

    def test_job_timeout_is_read_as_the_maximum(self, tmp_path: Path):
        """工作流里同时有 job 级 40 和步骤级 20 时，必须取 40（外层上界）。"""
        res = _run_script(
            tmp_path,
            CONCLUSION="cancelled",
            RUN_ATTEMPT="1",
            WORKFLOW_NAME="Update AI News Snapshot",
            MOCK_RUN_SECONDS=str(35 * 60),  # 35 分钟：按 40 算是 87.5%，按 20 算早就超了
        )
        assert _rerun_requested(tmp_path), f"未按 job 级超时判定: {res.stderr}"
        assert "declared job timeout 40m" in res.stderr


@needs_jq
class TestExistingBehaviourIsPreserved:
    """原有判定不能被这次改动破坏。"""

    def test_setup_phase_failure_is_retried(self, tmp_path: Path):
        res = _run_script(
            tmp_path,
            CONCLUSION="failure",
            RUN_ATTEMPT="1",
            WORKFLOW_NAME="Update AI News Snapshot",
            MOCK_CASE="setup",
        )
        assert _rerun_requested(tmp_path), f"setup 阶段失败未重试: {res.stderr}"

    def test_code_failure_is_never_retried(self, tmp_path: Path):
        res = _run_script(
            tmp_path,
            CONCLUSION="failure",
            RUN_ATTEMPT="1",
            WORKFLOW_NAME="Update AI News Snapshot",
            MOCK_CASE="code",
        )
        assert not _rerun_requested(tmp_path), "代码缺陷被重试了，会掩盖真实问题"
        assert "real defect" in res.stderr

    def test_attempt_cap_still_applies(self, tmp_path: Path):
        res = _run_script(
            tmp_path,
            CONCLUSION="failure",
            RUN_ATTEMPT="3",
            MAX_ATTEMPTS="3",
            WORKFLOW_NAME="Update AI News Snapshot",
            MOCK_CASE="setup",
        )
        assert not _rerun_requested(tmp_path), "重试上限失效，可能形成自喂循环"
        assert "reached the limit" in res.stderr

    def test_never_reacts_to_itself(self, tmp_path: Path):
        res = _run_script(
            tmp_path,
            CONCLUSION="failure",
            RUN_ATTEMPT="1",
            WORKFLOW_NAME="Retry Transient Failures",
            SELF_WORKFLOW_NAME="Retry Transient Failures",
            MOCK_CASE="setup",
        )
        assert not _rerun_requested(tmp_path), "自喂循环没有被拦住"
        assert "own failure" in res.stderr

    def test_dry_run_does_not_call_the_api(self, tmp_path: Path):
        res = _run_script(
            tmp_path,
            CONCLUSION="failure",
            RUN_ATTEMPT="1",
            WORKFLOW_NAME="Update AI News Snapshot",
            MOCK_CASE="setup",
            DRY_RUN="true",
        )
        assert not _rerun_requested(tmp_path), "DRY_RUN=true 时仍然调用了重跑 API"
        combined = (res.stdout + res.stderr).lower()
        assert "dry_run" in combined, f"没有留下 dry-run 的痕迹: {combined}"


class TestScriptIsSelfContained:
    """脚本刻意不依赖任何 action —— 这是它能在 action 解析失败时工作的前提。"""

    def test_script_uses_no_github_actions(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        body = text.split("jobs:", 1)[1]
        assert "uses:" not in body, (
            "该工作流声明了 uses: 步骤。恢复机制不能共享它要恢复的故障模式 —— "
            "action 解析 503 时，checkout 步骤会以同样的原因失败，恢复能力恰好在此刻消失"
        )

    def test_script_avoids_new_interpreter_dependencies(self):
        """日期运算用 coreutils 的 date，而不是引入 python3。"""
        body = SCRIPT.read_text(encoding="utf-8")
        code_lines = [ln for ln in body.splitlines() if not ln.strip().startswith("#")]
        joined = "\n".join(code_lines)
        assert "python3" not in joined, "脚本引入了 python3 依赖，超出预装工具范围"
        assert "date -d" in joined, "日期运算未使用 coreutils date"

#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Automatic re-run for infrastructure-caused workflow failures.
#
# First principles:
#   A job that fails inside "Set up job" has not executed a single line of
#   repository code. The runner was still resolving and downloading the actions
#   referenced by `uses:`. Such a failure therefore cannot be caused by this
#   repository, and it cannot be fixed by editing this repository. The only
#   correct response is to run the job again.
#
#   GitHub retries action resolution twice internally (roughly 20s and 26s) and
#   then fails the whole job permanently. There is no built-in job-level retry
#   for setup-phase failures, so recovery must be implemented explicitly.
#
#   Retrying must stay narrow: a genuine code failure retried three times only
#   wastes minutes and hides the real signal. So retry only when the failure
#   signature is provably external.
#
# Exit codes: 0 = handled (re-run requested or deliberately skipped)
#             1 = the script itself could not complete
# ---------------------------------------------------------------------------
set -euo pipefail

REPO="${REPO:?REPO is required, e.g. owner/name}"
RUN_ID="${RUN_ID:?RUN_ID is required}"
RUN_ATTEMPT="${RUN_ATTEMPT:-1}"
CONCLUSION="${CONCLUSION:-}"
WORKFLOW_NAME="${WORKFLOW_NAME:-unknown}"
SELF_WORKFLOW_NAME="${SELF_WORKFLOW_NAME:-}"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-3}"
DRY_RUN="${DRY_RUN:-false}"
DEFAULT_BRANCH="${DEFAULT_BRANCH:-main}"
# 超时 kill 的重试上限比普通故障更小：一次超时可能是上游抖动，连续两次同样的超时
# 几乎只能是确定性缺陷，再跑一次只是又烧掉一整个超时预算（这里就是 40 分钟）。
MAX_TIMEOUT_ATTEMPTS="${MAX_TIMEOUT_ATTEMPTS:-2}"
# 运行时长达到声明超时的百分之多少即判定为「被超时 kill」。留 20% 余量是因为
# 调度与清理有开销，实测 40 分钟的 job 落在 40m22s~40m24s。
TIMEOUT_TOLERANCE_PCT="${TIMEOUT_TOLERANCE_PCT:-80}"

log() { printf '%s\n' "$*" >&2; }
summary() { [ -n "${GITHUB_STEP_SUMMARY:-}" ] && printf '%s\n' "$*" >> "$GITHUB_STEP_SUMMARY"; return 0; }

if ! [[ "$RUN_ATTEMPT" =~ ^[0-9]+$ ]]; then RUN_ATTEMPT=1; fi
if ! [[ "$MAX_ATTEMPTS" =~ ^[0-9]+$ ]]; then MAX_ATTEMPTS=3; fi
# Only the literal string "true" enables dry-run, so an unexpected value never
# silently disables recovery.
[ "$DRY_RUN" = "true" ] || DRY_RUN=false

log "run=$RUN_ID attempt=$RUN_ATTEMPT/$MAX_ATTEMPTS workflow='$WORKFLOW_NAME' conclusion='$CONCLUSION'"

decide_skip() {
  log "decision: no re-run ($1)"
  summary "### Automatic re-run skipped"
  summary ""
  summary "- Workflow: \`$WORKFLOW_NAME\`"
  summary "- Reason: $1"
  exit 0
}

# ---------------------------------------------------------------------------
# 判定一个 cancelled 运行是「被超时 kill」还是「人的决定」
# ---------------------------------------------------------------------------
# GitHub 对两者给的都是 conclusion=cancelled —— job 超出 timeout-minutes 并没有
# 独立的 timed_out 结论（本仓库实测：0 个 timed_out、271 个 cancelled，且每个
# cancelled 都恰好停在超时线上）。日志里也没有区分特征，两边都是
# "##[error]The operation was canceled."。
#
# 唯一可用的判据是**时长**：被 kill 的运行会停在超时线上，人的取消落在任意时刻。
# 成功时导出 JOB_TIMEOUT_MIN（工作流声明的最外层超时，分钟）。
is_timeout_kill() {
  local meta started updated path
  meta=$(gh api "/repos/$REPO/actions/runs/$RUN_ID" 2>/dev/null) || {
    log "could not read run metadata"; return 1; }
  started=$(printf '%s' "$meta" | jq -r '.run_started_at // empty')
  updated=$(printf '%s' "$meta" | jq -r '.updated_at // empty')
  path=$(printf '%s' "$meta" | jq -r '.path // empty')
  if [ -z "$started" ] || [ -z "$updated" ] || [ -z "$path" ]; then
    log "run metadata incomplete; cannot classify cancelled run"
    return 1
  fi

  # GNU date 在 ubuntu-latest 与 Git Bash 上都可用，且比引入 python3 更符合
  # 本脚本「只依赖预装工具（gh / jq / git / coreutils）」的取向 —— 它刻意不声明
  # 任何 `uses:` 步骤，就是为了不在自己最该出手时被 action 解析失败拖下水。
  local started_epoch updated_epoch duration
  started_epoch=$(date -d "$started" +%s 2>/dev/null) || { log "cannot parse run_started_at='$started'"; return 1; }
  updated_epoch=$(date -d "$updated" +%s 2>/dev/null) || { log "cannot parse updated_at='$updated'"; return 1; }
  duration=$(( updated_epoch - started_epoch ))
  if [ "$duration" -lt 0 ]; then
    log "negative duration (${duration}s); clock or field mismatch"
    return 1
  fi

  # 读工作流文件里声明的超时。取**最大**值：job 级超时是外层上界，步骤级超时
  # 必须更小（有测试 test_step_timeout_leaves_room_for_downstream 守着），
  # 因此最大值就是 job 级超时。这样写不依赖 timeout-minutes 在文件中的先后顺序。
  local wf declared
  wf=$(gh api "/repos/$REPO/contents/$path?ref=$DEFAULT_BRANCH" --jq '.content' 2>/dev/null \
       | base64 -d 2>/dev/null) || { log "could not read $path"; return 1; }
  declared=$(printf '%s\n' "$wf" \
       | grep -oE '^[[:space:]]*timeout-minutes:[[:space:]]*[0-9]+' \
       | grep -oE '[0-9]+' | sort -n | tail -1)
  if [ -z "$declared" ]; then
    log "$path declares no timeout-minutes; cannot classify a cancelled run"
    return 1
  fi
  JOB_TIMEOUT_MIN="$declared"

  local threshold=$(( declared * 60 * TIMEOUT_TOLERANCE_PCT / 100 ))
  log "cancelled run ran ${duration}s; declared job timeout ${declared}m (threshold ${threshold}s)"
  [ "$duration" -ge "$threshold" ]
}

# --- 1. Only failures are eligible ----------------------------------------
# A cancelled run is normally an explicit human decision and must never be
# resurrected. But GitHub reports a job that exceeded `timeout-minutes` as
# `cancelled` too, so rejecting the conclusion outright makes this workflow
# blind to the one fault it most needs to see. Admit a cancelled run only when
# its duration proves it was cut off at the timeout.
TIMEOUT_KILL=false
case "$CONCLUSION" in
  failure) : ;;
  # A run whose jobs never got past provisioning is reported as
  # startup_failure, and a runner reclaimed mid-flight yields timed_out.
  # Both are exactly the infrastructure faults this workflow exists for, so
  # accepting only "failure" would blind it to its own purpose.
  startup_failure|timed_out) : ;;
  cancelled)
    if is_timeout_kill; then
      TIMEOUT_KILL=true
      log "classified as a timeout-kill, not a human decision"
    else
      decide_skip "cancelled before its timeout: a human decision, never resurrected"
    fi
    ;;
  "") decide_skip "conclusion unavailable" ;;
  *)  decide_skip "conclusion is '$CONCLUSION', not a retryable failure" ;;
esac

# --- 1b. Never react to itself --------------------------------------------
# If this workflow ever ends up in its own watch list, each failed retry run
# would spawn a fresh retry run with attempt=1, so the attempt cap could not
# bound it. Refuse structurally instead of trusting the trigger config.
if [ -n "$SELF_WORKFLOW_NAME" ] && [ "$WORKFLOW_NAME" = "$SELF_WORKFLOW_NAME" ]; then
  decide_skip "refusing to react to this workflow's own failure"
fi

# --- 2. Bounded retries ----------------------------------------------------
# Without a cap, a permanently broken external dependency becomes an infinite
# self-triggering loop, because each re-run emits another workflow_run event.
if [ "$RUN_ATTEMPT" -ge "$MAX_ATTEMPTS" ]; then
  decide_skip "attempt $RUN_ATTEMPT reached the limit of $MAX_ATTEMPTS"
fi

# --- 3. Classify the failure ----------------------------------------------
# A timeout-kill is already classified by its duration. It must be decided here
# rather than by the job classifier below, because the interrupted step's
# conclusion is `cancelled`, not `failure` — the classifier would count zero
# failed jobs and refuse to act, which is exactly how this workflow stayed
# silent through every hang it was built to recover from.
REASON=""
if [ "$TIMEOUT_KILL" = "true" ]; then
  # Retry once. One timeout may be an upstream stall; two identical timeouts
  # are a deterministic defect, and a third attempt would only burn another
  # full timeout budget (40 minutes here) for an identical outcome.
  if [ "$RUN_ATTEMPT" -ge "$MAX_TIMEOUT_ATTEMPTS" ]; then
    decide_skip "timeout-kill at attempt $RUN_ATTEMPT/$MAX_TIMEOUT_ATTEMPTS; a repeated timeout indicates a deterministic defect, not a transient fault"
  fi
  REASON="job exceeded its ${JOB_TIMEOUT_MIN}m timeout with a step still running"
else
# Deterministic signal: the runner reports "Set up job" as a real step. If that
# step failed, action resolution or runner provisioning failed, and repository
# code never ran.
if ! gh api "/repos/$REPO/actions/runs/$RUN_ID/attempts/$RUN_ATTEMPT/jobs?per_page=100" \
      > /tmp/failed_jobs.json 2>/tmp/jobs_err.txt; then
  log "could not read job details: $(cat /tmp/jobs_err.txt)"
  # A startup_failure run often has no attempt sub-resource at all, because no
  # job was ever created. Treating that as "cannot judge" would permanently
  # disable recovery for the one failure mode this workflow targets, so fall
  # through to the log-signature check instead of skipping outright.
  if [ "$CONCLUSION" != "startup_failure" ]; then
    decide_skip "job details unavailable, refusing to guess"
  fi
  echo '{"jobs":[]}' > /tmp/failed_jobs.json
fi

# Each failed job falls into exactly one of three classes. Counting "not a
# setup failure" as "a code failure" would be wrong: a job that died before it
# could report any step has an empty steps array and belongs to neither class.
classify_jobs() {
  jq -r '
    [ .jobs[]? | select(.conclusion == "failure") ] as $failed
    | ($failed | map(select([.steps[]?
          | select(.name == "Set up job" and .conclusion == "failure")] | length > 0)) | length) as $setup
    | ($failed | map(select([.steps[]?
          | select(.name != "Set up job" and .conclusion == "failure")] | length > 0)) | length) as $code
    | ($failed | map(select((.steps | length) == 0)) | length) as $opaque
    | "\($setup) \($code) \($opaque)"
  ' /tmp/failed_jobs.json
}
# Materialise through a real file. Process substitution needs /dev/fd, which is
# not usable in every container, and it fails silently when it is missing.
classify_jobs > /tmp/job_classes.txt
read -r SETUP_FAILED CODE_FAILED OPAQUE_FAILED < /tmp/job_classes.txt

log "failed jobs: setup-phase=$SETUP_FAILED code-phase=$CODE_FAILED unreported=$OPAQUE_FAILED"

if [ "$SETUP_FAILED" -gt 0 ] && [ "$CODE_FAILED" -eq 0 ]; then
  REASON="every failed job failed during Set up job"
elif [ "$CODE_FAILED" -gt 0 ]; then
  decide_skip "$CODE_FAILED job(s) failed in repository steps; a re-run would hide a real defect"
else
  # No failed job reported any step. This happens when the runner dies before
  # it can publish step results, so fall back to the recorded failure log.
  if gh run view "$RUN_ID" --repo "$REPO" --log-failed > /tmp/failed_log.txt 2>/dev/null; then
    if grep -qE 'Failed to resolve action download info|Unable to resolve action|Service Unavailable|502 Bad Gateway|503 Service|runner has received a shutdown signal|lost communication with the server|Could not resolve host' /tmp/failed_log.txt; then
      REASON="log matched a known transient infrastructure signature"
    fi
  fi
  [ -n "$REASON" ] || decide_skip "failure signature is not recognisably infrastructural"
fi
fi  # end: non-timeout classification

# --- 4. Re-run ------------------------------------------------------------
log "decision: re-run ($REASON)"
if [ "$DRY_RUN" = "true" ]; then
  log "DRY_RUN=true, not calling the API"
  summary "### Automatic re-run (dry run)"
  summary ""
  summary "- Workflow: \`$WORKFLOW_NAME\`"
  summary "- Reason: $REASON"
  exit 0
fi

# --failed also re-runs jobs that were skipped because they depended on the
# failed job, which is exactly what a build/deploy pair needs.
if gh run rerun "$RUN_ID" --repo "$REPO" --failed 2>/tmp/rerun_err.txt; then
  log "re-run requested for run $RUN_ID"
  summary "### Automatic re-run requested"
  summary ""
  summary "- Workflow: \`$WORKFLOW_NAME\`"
  summary "- Attempt: $RUN_ATTEMPT of $MAX_ATTEMPTS"
  summary "- Reason: $REASON"
  exit 0
fi

err=$(cat /tmp/rerun_err.txt)
log "::error::re-run request failed: $err"
summary "### Automatic re-run failed"
summary ""
summary "- Workflow: \`$WORKFLOW_NAME\`"
summary "- Error: $err"
exit 1

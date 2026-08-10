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

# --- 1. Only failures are eligible ----------------------------------------
# A cancelled run is an explicit human decision and must never be resurrected.
case "$CONCLUSION" in
  failure) : ;;
  "") decide_skip "conclusion unavailable" ;;
  *)  decide_skip "conclusion is '$CONCLUSION', not a failure" ;;
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
# Deterministic signal: the runner reports "Set up job" as a real step. If that
# step failed, action resolution or runner provisioning failed, and repository
# code never ran.
if ! gh api "/repos/$REPO/actions/runs/$RUN_ID/attempts/$RUN_ATTEMPT/jobs?per_page=100" \
      > /tmp/failed_jobs.json 2>/tmp/jobs_err.txt; then
  log "could not read job details: $(cat /tmp/jobs_err.txt)"
  decide_skip "job details unavailable, refusing to guess"
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

REASON=""
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

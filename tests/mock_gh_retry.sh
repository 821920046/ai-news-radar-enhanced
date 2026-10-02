#!/usr/bin/env bash
# gh mock for retry_transient_failure.sh tests.
# MOCK_CASE selects the failure shape returned by the jobs API.
set -uo pipefail
ARGS="$*"

setup_failed_job() {
  cat <<'JSON'
{"jobs":[{"name":"build","conclusion":"failure","steps":[{"name":"Set up job","conclusion":"failure"}]},
          {"name":"deploy","conclusion":"skipped","steps":[]}]}
JSON
}
code_failed_job() {
  cat <<'JSON'
{"jobs":[{"name":"build","conclusion":"failure","steps":[{"name":"Set up job","conclusion":"success"},{"name":"Assemble site","conclusion":"failure"}]}]}
JSON
}
mixed_job() {
  cat <<'JSON'
{"jobs":[{"name":"build","conclusion":"failure","steps":[{"name":"Set up job","conclusion":"failure"}]},
          {"name":"test","conclusion":"failure","steps":[{"name":"Set up job","conclusion":"success"},{"name":"Run tests","conclusion":"failure"}]}]}
JSON
}
no_steps_job() {
  cat <<'JSON'
{"jobs":[{"name":"build","conclusion":"failure","steps":[]}]}
JSON
}

if [[ "$ARGS" == *"/jobs"* ]]; then
  case "${MOCK_CASE:-setup}" in
    api_error) echo "gh: Service Unavailable (HTTP 503)" >&2; exit 1 ;;
    code)      code_failed_job ;;
    mixed)     mixed_job ;;
    nosteps|nosteps_transient|nosteps_opaque) no_steps_job ;;
    *)         setup_failed_job ;;
  esac
  exit 0
fi

# 运行元数据：用于判定 cancelled 究竟是「超时 kill」还是「人的决定」。
# MOCK_RUN_SECONDS 给出时长，两个时间戳由同一个基准时刻推导，保证自洽。
if [[ "$ARGS" == *"/actions/runs/"* ]]; then
  if [ "${MOCK_RUN_META_MISSING:-0}" = "1" ]; then
    echo '{"run_started_at":null,"updated_at":null,"path":null}'
    exit 0
  fi
  base=1780000000
  secs="${MOCK_RUN_SECONDS:-0}"
  echo "{\"run_started_at\":\"$(date -u -d "@$base" +%Y-%m-%dT%H:%M:%SZ)\",\"updated_at\":\"$(date -u -d "@$((base + secs))" +%Y-%m-%dT%H:%M:%SZ)\",\"path\":\"${MOCK_RUN_PATH:-.github/workflows/update-news.yml}\"}"
  exit 0
fi

# 工作流文件内容（base64）。脚本用 --jq '.content' 取值，所以这里直接输出 base64。
if [[ "$ARGS" == *"/contents/"* ]]; then
  printf '%s' "${MOCK_WORKFLOW_CONTENT:-}" | base64 | tr -d '\n'
  echo
  exit 0
fi

if [[ "$ARGS" == *"--log-failed"* ]]; then
  case "${MOCK_CASE:-setup}" in
    nosteps_transient) echo "##[error]Failed to resolve action download info. Error: Service Unavailable" ;;
    nosteps_opaque)    echo "##[error]something specific to this repository went wrong" ;;
    *)                 exit 1 ;;
  esac
  exit 0
fi

if [[ "$ARGS" == *"run rerun"* ]]; then
  if [ "${MOCK_RERUN_FAIL:-0}" = "1" ]; then
    echo "gh: run cannot be rerun (HTTP 403)" >&2; exit 1
  fi
  echo "rerun requested" >> "${MOCK_RERUN_LOG:-/tmp/rerun.txt}"
  exit 0
fi

echo "unhandled: $ARGS" >&2
exit 1

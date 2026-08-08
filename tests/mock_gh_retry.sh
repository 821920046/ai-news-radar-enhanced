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

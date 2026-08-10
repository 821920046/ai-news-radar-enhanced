#!/usr/bin/env bash
# 对抗审查用的 gh 模拟器 v2
# 新增：run 状态实时查询（TOCTOU）、HTTP 403/429/401 错误分类
set -uo pipefail
# Default every knob the harness may not export. `set -u` turns a missing one
# into an opaque failure that looks like a bug in the script under test.
: "${MOCK_DELETED:=/tmp/deleted.txt}"
: "${MOCK_ARTIFACTS:=/tmp/mock_artifacts.json}"
: "${MOCK_MODE:=normal}"
: "${MOCK_RERUN:=}"
ARG_ALL="$*"

# --- 单个 run 状态查询（删前重核）---
if [[ "$ARG_ALL" =~ /actions/runs/([0-9]+) ]]; then
  rid="${BASH_REMATCH[1]}"
  # MOCK_RERUN 里的 run 模拟“快照后被重新激活”
  if [[ " ${MOCK_RERUN:-} " == *" $rid "* ]]; then echo "in_progress"; else echo "completed"; fi
  exit 0
fi

if [[ "$ARG_ALL" == *"/actions/artifacts"* && "$ARG_ALL" != *"-X DELETE"* ]]; then
  jq -c '.artifacts[] | {id, name, size: .size_in_bytes, created_at, expired, run_id: .workflow_run.id}' "$MOCK_ARTIFACTS"
  exit 0
fi

if [[ "$ARG_ALL" == *"/actions/runs?status="* ]]; then
  st=$(sed -E 's/.*status=([a-z_]+).*/\1/' <<< "$ARG_ALL")
  case "$st" in
    in_progress) echo 9001 ;;
    queued)      echo 9002 ;;
    waiting)     echo 9003 ;;
    *)           echo "gh: HTTP 422 invalid status" >&2; exit 1 ;;
  esac
  exit 0
fi

if [[ "$ARG_ALL" == *"-X DELETE"* ]]; then
  id=$(sed -E 's#.*/artifacts/([0-9]+).*#\1#' <<< "$ARG_ALL")
  case "${MOCK_MODE:-normal}" in
    fatal403) echo "gh: Resource not accessible by integration (HTTP 403)" >&2; exit 1 ;;
    nohttp) echo "gh: network connection reset" >&2; exit 1 ;;
    server5xx) echo "gh: upstream service unavailable (HTTP 502)" >&2; exit 1 ;;
    ratelimit)
      # 前 2 次 429，之后成功 -> 验证退避重试
      n=$(cat "${MOCK_RL_COUNTER:-/tmp/rl}" 2>/dev/null || echo 0); n=$((n+1))
      echo "$n" > "${MOCK_RL_COUNTER:-/tmp/rl}"
      if [ "$n" -le 2 ]; then echo "gh: You have exceeded a secondary rate limit (HTTP 403)" >&2; exit 1; fi ;;
    ratelimit_hard) echo "gh: API rate limit exceeded (HTTP 429)" >&2; exit 1 ;;
  esac
  if [ "$id" = "777" ]; then echo "gh: Not Found (HTTP 404)" >&2; exit 1; fi
  echo "$id" >> "$MOCK_DELETED"
  exit 0
fi

echo "unhandled: $ARG_ALL" >&2
exit 1

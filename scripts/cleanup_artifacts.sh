#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 定时清理 GitHub Actions artifacts  (v2)
#
# 第一性原理：
#   artifact 存在的唯一目的是“在 job 之间传递产物 + 保留回滚能力”。
#   因此可删 = 既不参与任何活跃 run，也不是想保留的回滚点。
#   错误代价不对称：少删 = 浪费几 MB；多删 = 线上部署直接失败。
#   所以一切不确定均向“不删”倒。
#
# v2 相对 v1 的改进：
#   1. 关闭 TOCTOU 窗口：每个候选在 DELETE 前重新核对其 run 状态
#   2. 区分 HTTP 错误类型：404 容忍 / 429·限流退避重试 / 401·403 立即终止
#   3. 删除配额 + 时间预算，避免首次跑存量时撞 secondary rate limit
#   4. 失败率过高时以非 0 退出，不再静默“假成功”
#
# 依赖：gh (ubuntu-latest 预装)、jq
# 权限：GITHUB_TOKEN 需 actions: write
# ---------------------------------------------------------------------------
set -euo pipefail

REPO="${REPO:?REPO is required, e.g. owner/name}"
KEEP_HOURS="${KEEP_HOURS:-24}"
KEEP_LATEST_PER_NAME="${KEEP_LATEST_PER_NAME:-1}"
MAX_DELETES="${MAX_DELETES:-200}"          # 单次删除上限（限流护栏）
TIME_BUDGET_SEC="${TIME_BUDGET_SEC:-600}"  # 删除阶段时间预算
FAIL_RATE_ABORT="${FAIL_RATE_ABORT:-50}"   # 失败率百分比阈值，超过则报错退出
DRY_RUN="${DRY_RUN:-true}"
SELF_RUN_ID="${GITHUB_RUN_ID:-0}"

log() { printf '%s\n' "$*" >&2; }

# --- 0. 入参清洗 -----------------------------------------------------------
# workflow_dispatch 的 input 是自由文本，非数字会把 jq --argjson 炸成 exit 2，
# 而且日志上看不出是参数错。先校验并回退到安全默认值。
is_uint() { [[ "$1" =~ ^[0-9]+$ ]]; }
sanitize() { # name defaultValue -> echo cleaned
  local n="$1" v="$2" d="$3"
  if is_uint "$v"; then printf '%s' "$v"
  else log "::warning::$n='$v' 不是非负整数，回退为 $d"; printf '%s' "$d"; fi
}
KEEP_HOURS=$(sanitize KEEP_HOURS "$KEEP_HOURS" 24)
KEEP_LATEST_PER_NAME=$(sanitize KEEP_LATEST_PER_NAME "$KEEP_LATEST_PER_NAME" 1)
MAX_DELETES=$(sanitize MAX_DELETES "$MAX_DELETES" 200)
TIME_BUDGET_SEC=$(sanitize TIME_BUDGET_SEC "$TIME_BUDGET_SEC" 600)
FAIL_RATE_ABORT=$(sanitize FAIL_RATE_ABORT "$FAIL_RATE_ABORT" 50)
# DRY_RUN 只认字面量 "false"；其余一律当 true（fail-safe：拿不准就不删）
[ "$DRY_RUN" = "false" ] || DRY_RUN=true
log "config: KEEP_HOURS=$KEEP_HOURS KEEP_N=$KEEP_LATEST_PER_NAME MAX_DELETES=$MAX_DELETES DRY_RUN=$DRY_RUN"

# --- 带错误分类的 gh 调用 -------------------------------------------------
# 返回值：0=成功  1=已不存在(404/410)  2=限流(可重试)  3=致命(权限/认证)  4=其余
gh_delete() {
  local id="$1" err code
  err=$(timeout 30s gh api -X DELETE "/repos/$REPO/actions/artifacts/$id" --silent 2>&1 >/dev/null) || true
  [ -z "$err" ] && return 0
  code=$(grep -oE 'HTTP [0-9]{3}' <<<"$err" | head -1 | awk '{print $2}')
  case "$code" in
    20*)      return 0 ;;
    "")      # 非空错误却没有 HTTP 状态码（超时、DNS、CLI 输出格式变化）
              # 绝不能伪装成成功；按未知失败处理，安全方向是不误报删除。
              log "      ? no HTTP status: $err"; return 4 ;;
    404|410)  return 1 ;;
    429)      return 2 ;;
    403)      # 403 既可能是限流也可能是权限不足，必须看正文区分
              if grep -qiE 'rate limit|secondary|abuse' <<<"$err"; then return 2; else
                log "      FATAL: 403 且非限流 -> 几乎确定是 permissions 缺 actions:write"
                log "      $err"; return 3; fi ;;
    401)      log "      FATAL: 401 token 无效"; return 3 ;;
    5*)       return 2 ;;
    *)        log "      ? unexpected: $err"; return 4 ;;
  esac
}

# --- 1. 快照：一次性拉完全部 artifact ---------------------------------------
# 边分页边删会造成列表偏移、漏删一半。必须先实物化。
log "[1/5] fetching artifact list..."
timeout 120s gh api --paginate "/repos/$REPO/actions/artifacts?per_page=100" \
  -q '.artifacts[] | {id, name, size: .size_in_bytes, created_at, expired, run_id: .workflow_run.id}' \
  | jq -s '.' > /tmp/artifacts.json

TOTAL=$(jq 'length' /tmp/artifacts.json)
TOTAL_BYTES=$(jq '[.[] | select(.expired | not) | .size] | add // 0' /tmp/artifacts.json)
log "    total=$TOTAL  live_size=$(( TOTAL_BYTES / 1048576 ))MB"
if [ "$TOTAL" -eq 0 ]; then log "nothing to do"; exit 0; fi

# --- 2. 快照：所有非终态 run -------------------------------------------------
log "[2/5] fetching active runs..."
: > /tmp/active_runs.txt
for st in queued in_progress waiting; do
  timeout 60s gh api --paginate "/repos/$REPO/actions/runs?status=$st&per_page=100" \
    -q '.workflow_runs[].id' >> /tmp/active_runs.txt 2>/dev/null || true
done
echo "$SELF_RUN_ID" >> /tmp/active_runs.txt   # 永远排除自己
sort -u /tmp/active_runs.txt | grep -E '^[0-9]+$' > /tmp/active_runs_uniq.txt || true
# 实物化成真实文件：不能用 <(...) 进程替代喂 jq --slurpfile，
# jq 会延迟打开 /dev/fd/NN，此时 fd 已关 -> "Could not open /dev/fd/63"
jq -R 'tonumber' /tmp/active_runs_uniq.txt | jq -s '.' > /tmp/active_runs.json
log "    protected runs: $(jq 'length' /tmp/active_runs.json)"

# --- 3. 计算候选名单 ---------------------------------------------------------
# 保留规则（任一命中即保留）：
#   a) expired=true      GitHub 已回收存储，DELETE 必 404，删了无收益
#   b) 年龄 < KEEP_HOURS  时间缓冲带
#   c) 属于活跃 run      硬保护，删了等于杀掉线上部署
#   d) 同名最新 N 份     保留 Re-run deploy 的回滚能力
log "[3/5] computing candidates..."
jq --argjson keepHours "$KEEP_HOURS" \
   --argjson keepN "$KEEP_LATEST_PER_NAME" \
   --slurpfile active /tmp/active_runs.json '
  ($active[0] // []) as $act
  | (now - ($keepHours * 3600)) as $cutoff
  | group_by(.name)
  | map(sort_by(.created_at) | reverse | to_entries
        | map(.value + {rank: .key}))
  | flatten
  | map(select(
        (.expired | not)
        and ((.created_at | fromdateiso8601) < $cutoff)
        and (([.run_id] - $act) | length) == 1
        and (.rank >= $keepN)
      ))
  | sort_by(.created_at)
' /tmp/artifacts.json > /tmp/candidates.json

CAND=$(jq 'length' /tmp/candidates.json)
# 应用删除配额（从最旧的开始删，没删完的下次接着删）
jq --argjson n "$MAX_DELETES" '.[0:$n]' /tmp/candidates.json > /tmp/to_delete.json
COUNT=$(jq 'length' /tmp/to_delete.json)
BYTES=$(jq '[.[].size] | add // 0' /tmp/to_delete.json)
if [ "$CAND" -gt "$COUNT" ]; then
  log "    candidates=$CAND，受 MAX_DELETES=$MAX_DELETES 限制，本次只处理最旧的 $COUNT 份"
fi
log "    to delete: $COUNT artifacts, $(( BYTES / 1048576 ))MB"
jq -r '.[] | "      - #\(.id) \(.name) \(.created_at) \((.size/1048576*10|floor)/10)MB run=\(.run_id)"' \
  /tmp/to_delete.json >&2

# --- 4. 执行删除 -------------------------------------------------------------
DELETED=0; GONE=0; FAILED=0; SKIPPED_RACE=0; ABORTED=""

if [ "$DRY_RUN" = "true" ]; then
  log "[4/5] DRY_RUN=true — 未删除任何东西"
elif [ "$COUNT" -eq 0 ]; then
  log "[4/5] 无可删对象"
else
  log "[4/5] deleting..."
  START_TS=$(date +%s)
  # 同样不用 < <(...)：部分 shell/容器环境没有可用 /dev/fd，会报
  # "/dev/fd/63: No such file or directory" 并静默中断删除
  jq -r '.[] | "\(.id) \(.run_id)"' /tmp/to_delete.json > /tmp/to_delete_ids.txt
  : > /tmp/runstate_cache.txt

  while read -r id run_id; do
    [ -n "$id" ] || continue

    # 预算守卫：宁可本次少删，也不能把 job 拖超时
    if [ $(( $(date +%s) - START_TS )) -ge "$TIME_BUDGET_SEC" ]; then
      log "      ⏱ 时间预算用尽，剩余留给下次"; break
    fi

    # ★ TOCTOU 关闭：列表快照到现在可能已过去几十秒，run 可能被 re-run。
    # 删前再核对一次实时状态，只有 completed 才动手。
    st=$(grep -m1 "^$run_id " /tmp/runstate_cache.txt | cut -d' ' -f2 || true)
    if [ -z "$st" ]; then
      st=$(timeout 30s gh api "/repos/$REPO/actions/runs/$run_id" -q '.status' 2>/dev/null || echo unknown)
      echo "$run_id $st" >> /tmp/runstate_cache.txt
    fi
    if [ "$st" != "completed" ]; then
      log "      ↷ skip #$id: run $run_id 现在是 '$st'（快照后被重新激活）"
      SKIPPED_RACE=$((SKIPPED_RACE+1)); continue
    fi

    # 带退避的重试
    attempt=0; backoff=2
    while : ; do
      set +e; gh_delete "$id"; rc=$?; set -e
      case "$rc" in
        0) DELETED=$((DELETED+1)); break ;;
        1) GONE=$((GONE+1)); break ;;                      # 已不存在，等同成功
        2) attempt=$((attempt+1))
           if [ "$attempt" -ge 4 ]; then
             log "      ! #$id 限流重试 4 次仍失败，放弃后续"
             FAILED=$((FAILED+1)); ABORTED="rate_limit"; break
           fi
           log "      ↺ #$id 限流，${backoff}s 后重试 ($attempt/3)"
           sleep "$backoff"; backoff=$((backoff*3)) ;;
        3) ABORTED="fatal"; FAILED=$((FAILED+1)); break ;;  # 权限问题，重试无意义
        *) FAILED=$((FAILED+1)); break ;;
      esac
    done
    [ "$ABORTED" = "fatal" ] && { log "      ⏹ 致命错误，立即停止"; break; }
    [ "$ABORTED" = "rate_limit" ] && { log "      ⏹ 持续限流，停止本轮"; break; }
    sleep 0.3
  done < /tmp/to_delete_ids.txt

  log "    deleted=$DELETED gone=$GONE race_skip=$SKIPPED_RACE failed=$FAILED"
fi

# --- 5. 汇报与退出码 ---------------------------------------------------------
log "[5/5] summary"
ATTEMPTED=$(( DELETED + GONE + FAILED ))
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  {
    echo "### 🧹 Artifact cleanup"
    echo
    echo "| 项 | 值 |"
    echo "| --- | --- |"
    echo "| 仓库 artifact 总数 | $TOTAL |"
    echo "| 占用（未过期） | $(( TOTAL_BYTES / 1048576 )) MB |"
    echo "| 候选 / 本次处理 | $CAND / $COUNT |"
    echo "| 实际删除 | $DELETED |"
    echo "| 已不存在(404) | $GONE |"
    echo "| 竞态跳过 | $SKIPPED_RACE |"
    echo "| 失败 | $FAILED |"
    echo "| 预估释放 | $(( BYTES / 1048576 )) MB |"
    echo "| 保留策略 | ${KEEP_HOURS}h / 每名最新 ${KEEP_LATEST_PER_NAME} 份 |"
    echo "| DRY_RUN | $DRY_RUN |"
    [ -n "$ABORTED" ] && { echo; echo "> ⚠️ 本轮提前终止：\`$ABORTED\`"; }
  } >> "$GITHUB_STEP_SUMMARY"
fi

# 不再静默“假成功”：权限错或失败率过高必须让 workflow 变红
if [ "$ABORTED" = "fatal" ]; then
  log "::error::删除被权限/认证错误中断，检查 workflow 的 permissions: actions: write"
  exit 1
fi
if [ "$ATTEMPTED" -gt 0 ]; then
  rate=$(( FAILED * 100 / ATTEMPTED ))
  if [ "$rate" -ge "$FAIL_RATE_ABORT" ]; then
    log "::error::失败率 ${rate}% 超过阈值 ${FAIL_RATE_ABORT}%"
    exit 1
  fi
fi
exit 0

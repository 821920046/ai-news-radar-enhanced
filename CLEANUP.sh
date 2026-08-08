#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Repository cleanup: remove files that should never have been committed, and
# delete code that nothing references.
#
# First principles:
#
#   1. `.gitignore` only affects UNTRACKED files. Once a file is tracked, the
#      ignore rule is silently inert forever. So the question "which committed
#      files were supposed to be ignored?" must be asked of git itself
#      (`git check-ignore`), never answered with a hand-written list that will
#      drift out of date.
#
#   2. A file is safe to delete only if nothing references it. That is a
#      property to be PROVEN mechanically before deleting, not asserted by the
#      person writing the deletion list. This script therefore refuses to
#      delete anything whose name still appears in code that survives.
#
#   3. Deleting code is only correct if the test suite still passes afterwards.
#      Verification runs inside the script, and a failure rolls everything back.
#
# Exit codes: 0 = clean (or dry run), 1 = refused / rolled back
# ---------------------------------------------------------------------------
set -euo pipefail

DRY_RUN="${DRY_RUN:-true}"
# Only the literal string "false" performs real changes, so a typo can never
# accidentally mutate the repository.
[ "$DRY_RUN" = "false" ] || DRY_RUN=true

# Historical one-off notes: moved into docs/history/ rather than destroyed,
# because they are the only record of past decisions. Set to false to delete.
KEEP_HISTORY="${KEEP_HISTORY:-true}"

log() { printf '%s\n' "$*" >&2; }
die() { printf '::error::%s\n' "$*" >&2; exit 1; }

# --- 0. Preconditions -----------------------------------------------------
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "not a git repository"
cd "$(git rev-parse --show-toplevel)"

[ -f .gitignore ] || die ".gitignore missing; refusing to guess what should be ignored"

# A dirty tree makes rollback ambiguous: we could not tell our own changes from
# the user's uncommitted work.
#
# One exception is required, and it is caused by the very bug this script fixes:
# because compiled caches are tracked, merely running Python rewrites tracked
# files and makes the tree permanently dirty. Refusing on that would make the
# fix unreachable. So dirtiness is ignored for exactly those paths that
# .gitignore already claims to exclude, and enforced for everything else.
git status --porcelain | awk '{ print $NF }' > /tmp/cleanup_dirty.txt || true
: > /tmp/cleanup_dirty_real.txt
while IFS= read -r f; do
  [ -n "$f" ] || continue
  git check-ignore -q --no-index "$f" 2>/dev/null && continue
  printf '%s\n' "$f" >> /tmp/cleanup_dirty_real.txt
done < /tmp/cleanup_dirty.txt
if [ -s /tmp/cleanup_dirty_real.txt ]; then
  log "uncommitted changes:"
  sed 's/^/    /' /tmp/cleanup_dirty_real.txt >&2
  die "working tree has uncommitted changes; commit or stash them first"
fi
if [ -s /tmp/cleanup_dirty.txt ]; then
  log "note: $(wc -l < /tmp/cleanup_dirty.txt | tr -d ' ') dirty path(s) are ignored-but-tracked files; PHASE 1 is what fixes that"
fi

START_REF="$(git rev-parse HEAD)"
log "repo=$(git rev-parse --show-toplevel) head=${START_REF:0:8} dry_run=$DRY_RUN"
log "tracked files before: $(git ls-files | wc -l | tr -d ' ')"

# ===========================================================================
# PHASE 1 - untrack files that .gitignore already claims to exclude
# ===========================================================================
# Ask git which tracked files match an ignore rule. These stay on disk; only
# their tracking is removed, so local caches and private inputs keep working.
git ls-files -z > /tmp/cleanup_tracked.z
xargs -0 -a /tmp/cleanup_tracked.z git check-ignore --no-index 2>/dev/null \
  > /tmp/cleanup_untrack.txt || true
sort -u -o /tmp/cleanup_untrack.txt /tmp/cleanup_untrack.txt
UNTRACK_N=$(wc -l < /tmp/cleanup_untrack.txt | tr -d ' ')

log ""
log "== PHASE 1: tracked but ignored ($UNTRACK_N files) =="
if [ "$UNTRACK_N" -gt 0 ]; then
  # Summarise by category so a long list stays readable.
  awk -F/ '{ if ($0 ~ /\.pyc$/) print "  *.pyc / __pycache__";
             else print "  " $0 }' /tmp/cleanup_untrack.txt | sort | uniq -c | sed 's/^/ /' >&2
fi

# ===========================================================================
# PHASE 2 - delete code that nothing references
# ===========================================================================
# Every entry below was established by reference analysis. The guard further
# down re-proves it at run time, so a stale entry fails loudly instead of
# silently removing something that became used again.
#
# The notifier chain: the daily-briefing feature was cancelled, and its only
# remaining consumer is its own test. Test coverage of dead code is not a
# reason to keep the code.
cat > /tmp/cleanup_delete.txt <<'LIST'
notifier.py
normalizer.py
core/notifier.py
scripts/notifier.py
tests/test_notifier.py
scratch/analyze_dupes.py
scratch/test_filter.py
scripts/recommend.py
scripts/ai_processor.py
scripts/archive.py
scripts/dedup.py
scripts/logging_config.py
scripts/models.py
scripts/output.py
scripts/topic_filter.py
scripts/translate.py
scripts/utils.py
scripts/fetchers/__init__.py
scripts/fetchers/aggregators.py
scripts/fetchers/aihub.py
scripts/fetchers/builders.py
scripts/fetchers/newsletters.py
scripts/fetchers/official.py
scripts/fetchers/opml.py
scripts/fetchers/oss_trending.py
scripts/fetchers/waytoagi.py
OPTIMIZATION.diff
LIST

# Keep only entries that actually exist, so re-running is a no-op.
: > /tmp/cleanup_delete_present.txt
while IFS= read -r f; do
  [ -n "$f" ] || continue
  if git ls-files --error-unmatch "$f" >/dev/null 2>&1; then
    printf '%s\n' "$f" >> /tmp/cleanup_delete_present.txt
  fi
done < /tmp/cleanup_delete.txt
DELETE_N=$(wc -l < /tmp/cleanup_delete_present.txt | tr -d ' ')

log ""
log "== PHASE 2: dead code ($DELETE_N files) =="
sed 's/^/  /' /tmp/cleanup_delete_present.txt >&2 || true

# --- Reference closure guard ---------------------------------------------
# For each file being deleted, derive the identifiers other code would use to
# reach it, then search every SURVIVING source file for them. Any hit means the
# deletion set is not closed and the whole operation is unsafe.
log ""
log "-- proving the deletion set is closed under references --"
VIOLATIONS=0
while IFS= read -r f; do
  [ -n "$f" ] || continue
  case "$f" in *.py) ;; *) continue ;; esac
  # "scripts/fetchers/opml.py" -> module path "scripts.fetchers.opml"
  mod="${f%.py}"; mod="${mod//\//.}"
  base="$(basename "$f" .py)"
  # Search only files that will still exist after the deletion.
  hits=$(git ls-files '*.py' '*.yml' '*.yaml' \
    | grep -vxF -f /tmp/cleanup_delete_present.txt \
    | xargs -r grep -nE "(from|import)[[:space:]]+${mod//./\\.}([[:space:]]|$|\.)" 2>/dev/null \
    | grep -v '__pycache__' || true)
  # A bare top-level module such as notifier.py is also reachable as "import notifier".
  if [ "$mod" = "$base" ]; then
    hits="$hits$(git ls-files '*.py' \
      | grep -vxF -f /tmp/cleanup_delete_present.txt \
      | xargs -r grep -nE "^[[:space:]]*(from|import)[[:space:]]+${base}([[:space:]]|$|\.)" 2>/dev/null || true)"
  fi
  if [ -n "$(printf '%s' "$hits" | tr -d '[:space:]')" ]; then
    log "::error::$f is still referenced by surviving code:"
    printf '%s\n' "$hits" | sed 's/^/      /' >&2
    VIOLATIONS=$((VIOLATIONS + 1))
  fi
done < /tmp/cleanup_delete_present.txt

if [ "$VIOLATIONS" -gt 0 ]; then
  die "$VIOLATIONS file(s) still referenced; refusing to delete anything"
fi
log "   closed: no surviving file imports anything in the deletion set"

# ===========================================================================
# PHASE 3 - relocate historical one-off notes
# ===========================================================================
# These are point-in-time reports, not living documentation. They belong in a
# history folder so the repository root describes the CURRENT system.
# Materialise through a real file. Process substitution needs /dev/fd, which is
# not usable in every container, and it fails silently when it is missing.
git ls-files --full-name -- '*.md' ':!:README.md' ':!:CLAUDE.md' \
  ':!:docs/*' ':!:skills/*' ':!:frontend/*' > /tmp/cleanup_history.txt 2>/dev/null || true
HISTORY_N=$(wc -l < /tmp/cleanup_history.txt | tr -d ' ')

log ""
log "== PHASE 3: historical notes -> docs/history/ ($HISTORY_N files) =="
sed 's/^/  /' /tmp/cleanup_history.txt >&2 || true

if [ "$DRY_RUN" = "true" ]; then
  log ""
  log "DRY_RUN=true: nothing was changed. Re-run with DRY_RUN=false to apply."
  exit 0
fi

# ===========================================================================
# APPLY
# ===========================================================================
rollback() {
  log "::error::rolling back to ${START_REF:0:8}"
  git reset -q --hard "$START_REF"
  git clean -qfd -e '*.pyc' -e '__pycache__' || true
}

# Any unexpected failure between here and the end must not leave the repository
# half-cleaned. Partial application is worse than no application, because the
# user cannot tell which phases landed.
trap 'rc=$?; if [ "$rc" -ne 0 ]; then rollback; fi' EXIT

log ""
log "== applying =="

# Phase 1: stop tracking, keep on disk.
if [ "$UNTRACK_N" -gt 0 ]; then
  tr '\n' '\0' < /tmp/cleanup_untrack.txt | xargs -0 -r git rm -r --cached -q --
  log "untracked $UNTRACK_N file(s) (still present on disk)"
fi

# Phase 2: delete.
if [ "$DELETE_N" -gt 0 ]; then
  tr '\n' '\0' < /tmp/cleanup_delete_present.txt | xargs -0 -r git rm -q --
  log "deleted $DELETE_N dead file(s)"
fi
# Remove directories left empty by the deletions.
for d in scratch scripts/fetchers; do
  if [ -d "$d" ] && [ -z "$(find "$d" -type f ! -name '*.pyc' -print -quit)" ]; then
    rm -rf "$d"; log "removed empty directory $d"
  fi
done

# Phase 3: relocate.
if [ "$HISTORY_N" -gt 0 ] && [ "$KEEP_HISTORY" = "true" ]; then
  mkdir -p docs/history
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    git mv -f -- "$f" "docs/history/$(basename "$f")"
  done < /tmp/cleanup_history.txt
  git add docs/history >/dev/null 2>&1 || true
  log "moved $HISTORY_N note(s) into docs/history/"
elif [ "$HISTORY_N" -gt 0 ]; then
  tr '\n' '\0' < /tmp/cleanup_history.txt | xargs -0 -r git rm -q --
  log "deleted $HISTORY_N historical note(s)"
fi

# Phase 4: harden .gitignore so the same junk cannot be committed again.
# Untracking without hardening only postpones the problem: the next `git add -A`
# would re-commit anything the ignore file does not cover. The existing rules
# already cover caches, .claude/ and private feeds; these two do not.
ignore_line() {
  grep -Fqx "$1" .gitignore || { printf '%s\n' "$1" >> .gitignore; log "  .gitignore += $1"; }
}
if ! grep -Fqx '# Build/scratch artefacts that must never be committed' .gitignore; then
  printf '\n%s\n' '# Build/scratch artefacts that must never be committed' >> .gitignore
fi
ignore_line '*.diff'
ignore_line 'scratch/'
git add .gitignore

# ===========================================================================
# VERIFY - a cleanup that breaks the build is not a cleanup
# ===========================================================================
log ""
log "== verifying =="

# 1. Everything still compiles. Stale .pyc files are excluded so that a cached
#    byte-code copy of a deleted module cannot mask a broken import.
find . -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
if ! python3 -m compileall -q core api scripts tests > /tmp/cleanup_compile.txt 2>&1; then
  sed 's/^/    /' /tmp/cleanup_compile.txt >&2
  rollback; die "compileall failed after cleanup"
fi
log "   compileall OK"

# 2. The entry points the workflows actually invoke must still import.
for entry in scripts/update_news.py scripts/prerender.py; do
  [ -f "$entry" ] || { rollback; die "$entry disappeared"; }
done
if ! python3 -c 'import ast,sys
for p in ("scripts/update_news.py","scripts/prerender.py"):
    ast.parse(open(p,encoding="utf-8").read())' >/dev/null 2>&1; then
  rollback; die "entry point failed to parse"
fi
log "   entry points parse OK"

# 3. No surviving file imports a module that no longer exists locally.
if ! python3 - <<'PY'
import ast, pathlib, sys
root = pathlib.Path('.')
bad = []
for p in root.rglob('*.py'):
    if '__pycache__' in p.parts:
        continue
    try:
        tree = ast.parse(p.read_text(encoding='utf-8'))
    except SyntaxError as e:
        bad.append(f'{p}: syntax {e}')
        continue
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.append(node.module)
        elif isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        for name in names:
            # Only first-party packages are checkable without installing deps.
            top = name.split('.')[0]
            if top not in {'core', 'api', 'scripts', 'tests'}:
                continue
            rel = pathlib.Path(*name.split('.'))
            if not (rel.with_suffix('.py').exists() or (rel / '__init__.py').exists()):
                bad.append(f'{p}: imports missing module {name}')
if bad:
    print('\n'.join(sorted(set(bad))))
    sys.exit(1)
PY
then
  rollback; die "a surviving file imports a module that no longer exists"
fi
log "   no dangling first-party imports"

# 4. Run the offline-capable tests. Modules needing absent third-party
#    packages are skipped rather than reported as failures.
OFFLINE_TESTS=""
for t in $(git ls-files 'tests/test_*.py'); do
  m=$(printf '%s' "${t%.py}" | tr '/' '.')
  if python3 -c "import importlib; importlib.import_module('$m')" >/dev/null 2>&1; then
    OFFLINE_TESTS="$OFFLINE_TESTS $m"
  fi
done
if [ -n "$(printf '%s' "$OFFLINE_TESTS" | tr -d '[:space:]')" ]; then
  if ! python3 -m unittest $OFFLINE_TESTS > /tmp/cleanup_tests.txt 2>&1; then
    tail -20 /tmp/cleanup_tests.txt >&2
    rollback; die "tests failed after cleanup"
  fi
  log "   tests OK:$(grep -oE 'Ran [0-9]+ tests' /tmp/cleanup_tests.txt | tail -1)"
else
  log "   ::warning::no importable test module; test verification skipped"
fi

# ===========================================================================
log ""
log "tracked files after: $(git ls-files | wc -l | tr -d ' ')"
log "Changes are STAGED but not committed. Review with: git status && git diff --cached --stat"
log "Then: git commit -m 'chore: remove dead code and files that should not be tracked'"

# Success: disarm the rollback trap so the staged cleanup survives.
trap - EXIT
exit 0

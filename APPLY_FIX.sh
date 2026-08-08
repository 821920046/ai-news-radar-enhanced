#!/usr/bin/env bash
# Run from the repository root after copying this bundle over it.
# The operation is fail-closed: pipeline state is pushed successfully before
# the large files are removed from main's index.
set -euo pipefail

[ -d .git ] || { echo "ERROR: run from repository root" >&2; exit 1; }
for f in data/archive.json data/trend_history.json data/title-zh-cache.json; do
  [ -f "$f" ] || echo "warning: $f is absent; continuing" >&2
done
for f in .github/workflows/deploy-pages.yml \
         .github/workflows/update-news.yml \
         .github/workflows/cleanup-artifacts.yml \
         .github/workflows/retry-transient-failures.yml \
         scripts/cleanup_artifacts.sh \
         scripts/retry_transient_failure.sh \
         CLEANUP.sh; do
  [ -f "$f" ] || { echo "ERROR: missing $f; copy bundle first" >&2; exit 1; }
done

# CLEANUP.sh requires a clean working tree, while this script stages changes.
# Running them in the wrong order makes the cleanup refuse to start, so state
# the ordering now rather than letting the user discover it later.
chmod +x CLEANUP.sh 2>/dev/null || true
echo "note: run ./CLEANUP.sh BEFORE this script, or after committing what this" >&2
echo "      script stages. See README-APPLY.md step 0." >&2

# 1) Persist current mutable state on a one-commit snapshot branch.
tmp=$(mktemp -d)
trap 'git worktree remove --force "$tmp" >/dev/null 2>&1 || true; rm -rf "$tmp"' EXIT
git worktree add --detach "$tmp" HEAD >/dev/null
root="$PWD"
(
  cd "$tmp"
  # Unique temporary branch name keeps the script idempotent when run twice.
  git checkout --orphan "pipeline-state-next-$$" >/dev/null
  git rm -rf . >/dev/null
  mkdir -p data
  for f in archive.json trend_history.json title-zh-cache.json; do
    [ -f "$root/data/$f" ] && cp "$root/data/$f" "data/$f"
  done
  git add data
  git config user.name "github-actions[bot]"
  git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
  git commit -m "state: initial pipeline snapshot" >/dev/null
  old=$(git ls-remote origin refs/heads/pipeline-state | awk '{print $1}')
  if [ -n "$old" ]; then
    git push --force-with-lease="refs/heads/pipeline-state:$old" \
      origin HEAD:refs/heads/pipeline-state
  else
    git push origin HEAD:refs/heads/pipeline-state
  fi
)

# 2) Stop tracking mutable state on main. This runs only after step 1 succeeds.
marker="# Mutable pipeline state lives on the pipeline-state branch"
touch .gitignore
if ! grep -Fqx "$marker" .gitignore; then
  {
    echo
    echo "$marker"
    echo "data/archive.json"
    echo "data/trend_history.json"
    echo "data/title-zh-cache.json"
  } >> .gitignore
fi
git rm --cached --ignore-unmatch \
  data/archive.json data/trend_history.json data/title-zh-cache.json
git add .gitignore .github/workflows scripts/cleanup_artifacts.sh scripts/retry_transient_failure.sh
chmod +x scripts/cleanup_artifacts.sh scripts/retry_transient_failure.sh
git update-index --chmod=+x scripts/cleanup_artifacts.sh scripts/retry_transient_failure.sh 2>/dev/null || true

echo
echo "Prepared successfully. Review, then commit and push:"
echo "  git diff --cached --stat"
echo "  git commit -m 'fix: harden Pages deployment and Actions storage'"
echo "  git push"

#!/usr/bin/env bash
# Produce the single hand-apply diff bundle from a throwaway git repo.
# Inputs  (tmpfs):  $WORKSPACE/input  = pristine uploaded files
#                   $WORKSPACE/work   = same tree after the agents edited it
# Output  (tmpfs):  $WORKSPACE/bundle/{changes.patch, files/, RUNBOOK.md}
#                   $WORKSPACE/bundle.tgz
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
INPUT="$WORKSPACE/input"
WORK="$WORKSPACE/work"
BUNDLE="$WORKSPACE/bundle"
TMP="$WORKSPACE/.difftmp"
BRIEF_FILE="${BRIEF_FILE:-$WORKSPACE/brief.txt}"

rm -rf "$BUNDLE" "$TMP"
mkdir -p "$BUNDLE/files" "$TMP"

export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null GIT_CONFIG_NOSYSTEM=1
cd "$TMP"
git init -q
git config user.email "agent-array@localhost"
git config user.name  "agent-array"
git config core.autocrlf false
# This repo runs in the container that holds TASK_TOKEN / LITELLM_KEY: no hooks,
# no fsmonitor command, and (below) no .git content from the upload or the models.
git config core.hooksPath /dev/null
git config core.fsmonitor false

# 1) baseline commit = pristine input. Any .git file/dir in the upload is skipped
#    (the panel already refuses them): a copied .git/config or hook would execute.
rsync -a --exclude='.git' "$INPUT/" ./
git add -A
git commit -qm "baseline" --allow-empty

# 2) overlay the edited tree onto the baseline. rsync --delete mirrors WORK so
#    adds, modifies AND deletes are all captured, while --exclude drops the test
#    artifacts the orchestrator created in WORK (.venv/, node_modules/, caches) so
#    they never leak into the patch or files/. '.git' (any depth) also protects
#    the destination .git from --delete and keeps nested repos out.
rsync -a --delete \
  --exclude='.git' \
  --exclude='.venv/' \
  --exclude='node_modules/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='.pytest_cache/' \
  --exclude='.mypy_cache/' \
  --exclude='.ruff_cache/' \
  --exclude='.tox/' \
  --exclude='.coverage' \
  --exclude='htmlcov/' \
  --exclude='*.egg-info/' \
  "$WORK/" ./
# (*.egg-info/: the orchestrator's `pip install -e .` writes <pkg>.egg-info into
#  WORK for any setuptools project; without this it shipped in every patch.)
# Defeat git's "racily clean" stat shortcut: a model may edit a file to the EXACT
# same byte size, and rsync -a preserves mtimes, so git's add can wrongly decide
# the file is unchanged and drop the edit from the patch. Bump every file's mtime
# so git re-hashes content and catches modifications regardless of size.
find . -path ./.git -prune -o -type f -exec touch {} +
git add -A

# 3) the unified patch (this is what the user applies with `git apply`)
git diff --cached --binary > "$BUNDLE/changes.patch"

# 4) files/ = full copy of every changed/added file, for eyeball + manual copy
git diff --cached --name-only --diff-filter=ACMR -z \
  | while IFS= read -r -d '' f; do
      mkdir -p "$BUNDLE/files/$(dirname "$f")"
      cp -a "$f" "$BUNDLE/files/$f"
    done

# 5) RUNBOOK.md -- how to apply, what changed, how it was verified
CHANGED=$(git diff --cached --name-status --diff-filter=ACMRD || true)
# Files that change what gets installed or executed outside this diff (dependency
# pins and registries, CI, git/editor hooks): call them out for a careful look.
SENSITIVE=$(git diff --cached --name-only --diff-filter=ACMRD | grep -E \
  '(^|/)(package(-lock)?\.json|npm-shrinkwrap\.json|yarn\.lock|pnpm-lock\.yaml|\.npmrc|\.yarnrc(\.yml)?|requirements[^/]*\.txt|pyproject\.toml|setup\.(py|cfg)|poetry\.lock|Pipfile(\.lock)?|uv\.lock|pip\.(conf|ini)|\.pypirc|go\.(mod|sum)|Cargo\.(toml|lock)|Gemfile(\.lock)?|Dockerfile|Makefile|\.gitattributes|\.gitmodules|\.pre-commit-config\.yaml)$|(^|/)\.(github|gitlab|circleci|husky|devcontainer|vscode)/|(^|/)\.gitlab-ci\.yml$' || true)
if [ -n "$SENSITIVE" ]; then
  SENSITIVE_MD="## Sensitive changes - review before applying
These files decide what gets installed, from where, or what runs in CI or on
your machine. A model (or a prompt-injected file) changed them; check each one.
\`\`\`
$SENSITIVE
\`\`\`
"
else
  SENSITIVE_MD=""
fi
cat > "$BUNDLE/RUNBOOK.md" <<EOF
# agent-array change bundle â€” task ${TASK_ID:-unknown}

Generated $(date -u +%Y-%m-%dT%H:%M:%SZ). Nothing was pushed or synced; apply by hand.

## Brief
$(sed 's/^/> /' "$BRIEF_FILE" 2>/dev/null || echo "> (none)")

## Files changed
\`\`\`
${CHANGED:-<none>}
\`\`\`

${SENSITIVE_MD}
## Apply
From the root of your copy of the original files:

\`\`\`bash
git apply --stat   changes.patch   # preview
git apply --check  changes.patch   # dry-run, must be clean
git apply          changes.patch   # apply
\`\`\`

If you are not in a git repo, or \`git apply\` rejects a hunk, copy the
corresponding file(s) from \`files/\` over your originals instead.

## Verification recorded by the pipeline
See \`VERIFY.txt\` in this bundle for the test output, the specification and intent-review verdicts.
EOF

# 6) verification log, if the orchestrator wrote one
[ -f "$WORKSPACE/VERIFY.txt" ] && cp -a "$WORKSPACE/VERIFY.txt" "$BUNDLE/VERIFY.txt"

# 7) single tarball for download
tar czf "$WORKSPACE/bundle.tgz" -C "$BUNDLE" .
echo "bundle ready: $WORKSPACE/bundle.tgz ($(du -h "$WORKSPACE/bundle.tgz" | cut -f1))"

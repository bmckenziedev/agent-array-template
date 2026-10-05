#!/usr/bin/env bash
# Thin wrapper the session Job's `session` container runs. Fetches input from the
# panel's INTERNAL port (PANEL_BASE = :8081), runs the stage orchestrator, builds
# the diff bundle, uploads it back. Everything lives in tmpfs and dies with the pod.
# This container holds TASK_TOKEN + LITELLM_KEY and never runs uploaded/generated
# code: tests run in the `tester` sidecar (tester.py). The panel serves input once.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
export HOME=/home/agent
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null

# On ANY exit (done, needs-review, or die()) wipe what this task wrote to the
# pod's scratch mounts. A finished pod keeps its emptyDirs -- the user's code,
# the venv and the bundle, in node RAM (medium: Memory) -- until the Job's
# ttlSecondsAfterFinished GC deletes it; the bundle already lives on the panel.
# Contents only (the mounts stay), never "/", and only files we own.
wipe() {
  local d
  for d in "$WORKSPACE" "$HOME" "${TMPDIR:-/tmp}"; do
    [ -n "$d" ] && [ "$d" != "/" ] && [ -d "$d" ] || continue
    find "$d" -mindepth 1 -maxdepth 1 -user "$(id -u)" -exec rm -rf -- {} + 2>/dev/null || true
  done
}
trap wipe EXIT

mkdir -p "$WORKSPACE/input" "$WORKSPACE/work"

AUTH=(-H "X-Task-Token: ${TASK_TOKEN}")

# Report a stage to the panel; never fatal (status is best-effort telemetry).
status() {
  curl -fsS "${AUTH[@]}" -X POST \
    "$PANEL_BASE/internal/tasks/$TASK_ID/status" \
    -H 'Content-Type: application/json' -d "{\"stage\":\"$1\"}" >/dev/null 2>&1 || true
}

# Fail a stage loudly: tell the panel, then exit non-zero.
die() { status "$1"; echo "[entrypoint] FAILED at: $1" >&2; exit 1; }

status "fetching"
# input.tgz = { brief.txt, files/... }  (files/ is the pristine upload tree)
curl -fsS "${AUTH[@]}" "$PANEL_BASE/internal/tasks/$TASK_ID/input" \
  -o "$WORKSPACE/input.tgz" || die "error-fetch"
tar xzf "$WORKSPACE/input.tgz" -C "$WORKSPACE"          # -> brief.txt + files/
cp -a "$WORKSPACE/files/." "$WORKSPACE/input/" 2>/dev/null || true
cp -a "$WORKSPACE/files/." "$WORKSPACE/work/"  2>/dev/null || true

# The orchestrator reports framing/spec/coding/verifying/repairing itself.
# The orchestrator exits 0 (verified) or 3 (emitted-but-needs-review); both still
# produce a bundle. `if` guards set -e so we can capture the code either way.
# Any other non-zero (crash) is a hard failure.
RC=0
if python3 /usr/local/bin/orchestrator.py; then
  RC=0
else
  RC=$?
  [ "$RC" -eq 3 ] || die "error-orchestrator"
fi

status "bundling"
/usr/local/bin/diff_bundler.sh || die "error-bundle"

# Raw gzip body (no multipart): the panel streams it to disk under its size cap
# and accepts exactly one bundle per task. Accepting it also ends the task
# (done / needs-review) and revokes TASK_TOKEN, so no status call follows.
curl -fsS "${AUTH[@]}" -X POST "$PANEL_BASE/internal/tasks/$TASK_ID/bundle" \
  -H 'Content-Type: application/gzip' \
  -H "X-Bundle-Verified: $([ "$RC" -eq 0 ] && echo true || echo false)" \
  --data-binary "@$WORKSPACE/bundle.tgz" >/dev/null || die "error-upload"
exit 0

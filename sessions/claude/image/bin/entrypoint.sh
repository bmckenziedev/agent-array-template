#!/usr/bin/env bash
set -euo pipefail
# Forge credentials come only from the per-user registry mount.
if [ -n "${AA_GIT_USERNAME:-}" ]; then
  git config --global user.name "${AA_USER:?}"
  git config --global user.email "${AA_GIT_EMAIL:?}"
  git config --global credential.helper /usr/local/bin/aa-git-credential
fi
role="${1:-claude}"
case "$role" in
  estate) exec aa-snapshot serve ;;
  usage) exec aa-usage-report --loop "${AA_USAGE_REPORT_INTERVAL_S:-300}" ;;
  egress) exec /usr/bin/python3 -I /opt/aa/bin/aa-kimi-egress ;;
  claude|check)
    entrypoint-check --tool claude
    python3 /opt/aa/bin/policy-check.py "${AA_POLICY_DIR:-/etc/claude-code}/managed-settings.json"
    [ "$role" != check ] || exit 0
    umask 077
    mkdir -p /work/tasks
    tmux -f /etc/tmux.conf new-session -d -s aa -n claude -c /work /usr/local/bin/aa-rc
    tmux new-window -d -t aa:1 -n shell -c /work
    trap 'tmux kill-server 2>/dev/null || true; exit 0' TERM INT
    while tmux has-session -t aa 2>/dev/null; do sleep 5; done
    ;;
  *) echo "unknown role: $role" >&2; exit 2 ;;
esac

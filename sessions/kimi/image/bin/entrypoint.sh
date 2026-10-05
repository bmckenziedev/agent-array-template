#!/usr/bin/env bash
set -euo pipefail
# Forge credentials come only from the per-user registry mount.
if [ -n "${AA_GIT_USERNAME:-}" ]; then
  git config --global user.name "${AA_USER:?}"
  git config --global user.email "${AA_GIT_EMAIL:?}"
  git config --global credential.helper /usr/local/bin/aa-git-credential
fi
role="${1:-kimi}"
case "$role" in
  estate) exec aa-snapshot serve ;;
  usage) exec aa-usage-report --loop "${AA_USAGE_REPORT_INTERVAL_S:-300}" ;;
  egress) exec /usr/bin/python3 -I /opt/aa/bin/aa-kimi-egress ;;
  kimi|check)
    entrypoint-check --tool kimi
    aa-kimi-config check
    [ "$role" != check ] || exit 0
    umask 077
    mkdir -p /work/tasks
    tmux -f /etc/tmux.conf new-session -d -s aa -n kimi -c /work /usr/local/bin/aa-kimi gate
    tmux new-window -d -t aa:1 -n shell -c /work
    trap 'tmux kill-server 2>/dev/null || true; exit 0' TERM INT
    while tmux has-session -t aa 2>/dev/null; do sleep 5; done
    ;;
  *) echo "unknown role: $role" >&2; exit 2 ;;
esac

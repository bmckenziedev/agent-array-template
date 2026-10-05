#!/usr/bin/env bash
set -euo pipefail
if [[ ${1:-} != --yes ]]; then
  printf 'Preview: install backup scripts and rendered units; enable timers only after credential setup.\n'
  exit 0
fi
src=$(cd "$(dirname "$0")" && pwd)
rendered=${2:?Provide rendered/files/ops/backup directory}
install -d -m 0700 /etc/cluster-ops
install -d -m 0755 /opt/cluster-ops/backup
install -m 0755 "$src"/*.sh "$src"/*.py /opt/cluster-ops/backup/
install -m 0600 "$rendered/backup.env" /etc/cluster-ops/backup.env
install -m 0600 "$rendered/ssh_config.conf" /etc/cluster-ops/ssh_config
install -m 0644 "$src"/units/*.service "$rendered"/units/*.timer /etc/systemd/system/
systemctl daemon-reload
printf 'Provision password, SSH identity and verified known_hosts; initialise restic separately; then enable timers.\n'

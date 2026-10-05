#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
printf 'Install hostwatch collector, service and timer on this Ubuntu/Debian host.\n'
if [[ $# == 0 ]]; then echo 'Add --yes to execute.'; exit 0; fi
[[ $# == 1 && $1 == --yes ]] || { echo 'Usage: install.sh [--yes]' >&2; exit 64; }
[[ $(id -u) == 0 ]] || { echo 'Root is required.' >&2; exit 1; }
# Packages are provisioned separately; installing this module never starts smartd.
install -d -m 0755 /usr/local/libexec /var/lib/hostwatch /var/lib/node_exporter/textfile_collector
install -m 0755 "$HERE/hostwatch-collect.sh" /usr/local/libexec/hostwatch-collect.sh
install -m 0644 "$HERE/hostwatch.service" "$HERE/hostwatch.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now hostwatch.timer
systemctl start hostwatch.service

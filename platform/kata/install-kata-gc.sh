#!/usr/bin/env bash
# Explicit authorization enables the timer; the collector itself remains fail-closed.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
yes=0
uninstall=0
run_now=0
for arg in "$@"; do
  case "$arg" in
    --yes) yes=1 ;;
    --uninstall) uninstall=1 ;;
    --run-now) run_now=1 ;;
    *) echo "usage: $0 [--uninstall|--run-now] --yes" >&2; exit 64 ;;
  esac
done
if (( ! yes )); then
  echo "Read-only plan: install Kata GC service and hourly timer; --yes required."
  exit 0
fi
[[ $(id -u) -eq 0 ]] || { echo "run as root" >&2; exit 1; }
textfile=${TEXTFILE_DIR:-/var/lib/node_exporter/textfile_collector}
if (( uninstall )); then
  systemctl disable --now kata-gc.timer
  rm -f /usr/local/sbin/kata-gc.sh /etc/systemd/system/kata-gc.service /etc/systemd/system/kata-gc.timer
  rm -f "$textfile/aa_kata_gc.prom"
  systemctl daemon-reload
  exit 0
fi
config=${KATA_GC_CONFIG:-/var/lib/rancher/k3s/agent/etc/containerd/config-v3.toml.d/20-kata.toml}
[[ -f $config ]] || { echo "Kata containerd drop-in missing: $config" >&2; exit 1; }
bash -n "$here/kata-gc.sh"
install -m 0755 "$here/kata-gc.sh" /usr/local/sbin/kata-gc.sh
for unit in kata-gc.service kata-gc.timer; do
  install -m 0644 "$here/$unit" "/etc/systemd/system/$unit"
done
install -d -m 0755 "$textfile"
systemctl daemon-reload
systemctl enable --now kata-gc.timer
if (( run_now )); then systemctl start kata-gc.service; fi

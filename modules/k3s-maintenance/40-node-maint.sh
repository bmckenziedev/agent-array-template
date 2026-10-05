#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
source "$here/lib.sh"
require_window "${1:-}"
load_node "${2:-}"
repo=$(cd "$here/../.." && pwd)
kubectl cordon "$NODE_NAME"
kubectl drain "$NODE_NAME" --ignore-daemonsets --timeout="${DRAIN_TIMEOUT:-300s}"
printf 'Drain completed; reboot is a separate explicit step.\n'

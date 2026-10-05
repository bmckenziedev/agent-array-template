#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
source "$here/lib.sh"
require_window "${1:-}"
load_node "${2:-}"
repo=$(cd "$here/../.." && pwd)
[[ $(hostname -s) == "${NODE_LOCAL_NAME:?}" ]] || { printf 'Run on the configured node only\n' >&2; exit 2; }
systemctl reboot

#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
source "$here/lib.sh"
require_window "${1:-}"
load_node "${2:-}"
repo=$(cd "$here/../.." && pwd)
[[ $NODE_CONTROL_PLANE == true ]] || exit 0
"$repo/ops/backup/backup.sh" --yes
"$repo/ops/backup/weekly-check.sh" --yes

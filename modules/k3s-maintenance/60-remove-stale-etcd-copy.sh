#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
source "$here/lib.sh"
require_window "${1:-}"
load_node "${2:-}"
repo=$(cd "$here/../.." && pwd)
printf 'Review stale copies; automatic deletion is deliberately disabled.\n'
find /var/lib/rancher/k3s/server/db -maxdepth 1 -name 'etcd-old-*' -type d -print

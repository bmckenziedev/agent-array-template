#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib.sh
source "$here/lib.sh"
require_window "${1:-}"
load_node "${2:-}"
printf 'Review stale copies; automatic deletion is deliberately disabled.\n'
find /var/lib/rancher/k3s/server/db -maxdepth 1 -name 'etcd-old-*' -type d -print

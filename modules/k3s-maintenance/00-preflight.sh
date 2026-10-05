#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib.sh
source "$here/lib.sh"
require_window "${1:-}"
load_node "${2:-}"
kubectl get nodes -o wide
k3s secrets-encrypt status
k3s etcd-snapshot ls

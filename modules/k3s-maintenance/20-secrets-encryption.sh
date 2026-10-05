#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib.sh
source "$here/lib.sh"
require_window "${1:-}"
load_node "${2:-}"
[[ $NODE_CONTROL_PLANE == true ]] || exit 0
k3s secrets-encrypt status
phase=${3:-enable}
case "$phase" in
  enable|prepare|rotate|reencrypt) k3s secrets-encrypt "$phase" ;;
  *) printf 'Unknown encryption phase\n' >&2; exit 2 ;;
esac
k3s secrets-encrypt status
printf 'Wait for all server encryption hashes to agree before the next coordinated phase.\n'

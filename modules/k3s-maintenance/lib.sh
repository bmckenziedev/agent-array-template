#!/usr/bin/env bash
set -euo pipefail
require_window() {
  if [[ ${1:-} != --i-am-in-the-window ]]; then
    printf 'Preview only: mutation requires --i-am-in-the-window.\n'
    exit 0
  fi
}
load_node() {
  local file=${1:?Provide rendered per-node env file}
  [[ -f $file ]] || { printf 'Missing node configuration\n' >&2; exit 2; }
  source "$file"
  : "${NODE_NAME:?}" "${NODE_ADDRESS:?}" "${NODE_ROLES_JSON:?}"
  NODE_CONTROL_PLANE=$(python3 -c 'import json,sys; print("true" if "control-plane" in json.loads(sys.argv[1]) else "false")' "$NODE_ROLES_JSON")
}

#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"
load_env "$@"
: "${RENDERED_DIR:?set RENDERED_DIR}"
cmd=(python cluster/node-contract/apply-node-contract.py --rendered "$RENDERED_DIR")
if $yes; then cmd+=(--yes); fi
"${cmd[@]}"

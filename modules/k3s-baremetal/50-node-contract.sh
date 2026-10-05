#!/usr/bin/env bash
set -euo pipefail
# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib.sh
source "$(dirname "$0")/lib.sh"
load_env "$@"
: "${RENDERED_DIR:?set RENDERED_DIR}"
cmd=(python cluster/node-contract/apply-node-contract.py --rendered "$RENDERED_DIR")
if $yes; then cmd+=(--yes); fi
"${cmd[@]}"

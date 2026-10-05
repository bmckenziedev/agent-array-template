#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"
load_env "$@"
run k3s --version
run kubectl get node "$NODE_NAME" -o wide
run kubectl get runtimeclasses
run kubectl get storageclasses
echo 'Verify OIDC subject/groups, Kata isolation, exec-guard denials and pod egress after each node IP change.'

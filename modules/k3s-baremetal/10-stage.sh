#!/usr/bin/env bash
set -euo pipefail
# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib.sh
source "$(dirname "$0")/lib.sh"
load_env "$@"
run install -d -m 0755 /etc/rancher/k3s/config.yaml.d
echo 'Stage the rendered OIDC drop-in and node contract; prepare encrypted login storage on session nodes.'
echo 'Verify virtualization, containerd Kata handlers, clock sync and non-overlapping CIDRs before install.'

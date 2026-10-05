#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib.sh
source "$here/lib.sh"
require_window "${1:-}"
load_node "${2:-}"
repo=$(cd "$here/../.." && pwd)
[[ $NODE_CONTROL_PLANE == true ]] || exit 0
rendered=${3:?Provide rendered/files/ops/audit directory}
python3 "$repo/ops/audit/audit_policy_check.py" "$rendered/audit-policy.yaml"
install -d -m 0700 /etc/cluster-ops /var/log/kubernetes
install -m 0600 "$rendered/audit-policy.yaml" /etc/cluster-ops/audit-policy.yaml
install -d -m 0755 /etc/rancher/k3s/config.yaml.d
install -m 0600 "$rendered/k3s-dropin.yaml" /etc/rancher/k3s/config.yaml.d/30-cluster-audit.yaml
printf 'Restart one server at a time after configuration review.\n'

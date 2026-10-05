#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
NS="${WAZUH_NAMESPACE:-agent-array-wazuh}"
UPSTREAM="${WAZUH_UPSTREAM:-}"
COMMIT=2c2d13c550248b1fe91a5c3e3671de06a6180769
FILES="$ROOT/rendered/files/modules/wazuh/overlay"
GLOBAL="$ROOT/rendered/global/modules/wazuh/k8s"
printf 'Render pinned Wazuh overlay; server dry-run, then apply server and agent manifests.\n'
if [[ $# == 0 ]]; then echo 'Add --yes to execute; see README for manual prerequisites.'; exit 0; fi
[[ $# == 1 && $1 == --yes ]] || { echo 'Usage: install.sh [--yes]' >&2; exit 64; }
[[ -n $UPSTREAM && -d $UPSTREAM/wazuh && -d $FILES && -d $GLOBAL ]] || {
  echo 'Supply WAZUH_UPSTREAM and render the organization first.' >&2; exit 1;
}
[[ $(git -C "$UPSTREAM" rev-parse HEAD) == "$COMMIT" ]] || { echo 'Upstream commit mismatch.' >&2; exit 1; }
[[ -z $(git -C "$UPSTREAM" diff --name-only) ]] || { echo 'Upstream tracked files are modified.' >&2; exit 1; }
# Credentials, PKI and hashed internal_users.yml are provisioned outside this checkout.
for secret in indexer-cred dashboard-cred wazuh-api-cred wazuh-authd-pass wazuh-cluster-key; do
  kubectl -n "$NS" get secret "$secret" -o name >/dev/null
done
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/envs/organization"
cp -R "$UPSTREAM/wazuh" "$work/wazuh"
cp "$FILES/"* "$work/envs/organization/"
cp "$GLOBAL/networkpolicy.yaml" "$work/envs/organization/networkpolicy.yaml"
[[ -r ${WAZUH_INTERNAL_USERS:-} ]] || { echo 'Set WAZUH_INTERNAL_USERS to the private bcrypt configuration.' >&2; exit 1; }
cp "$WAZUH_INTERNAL_USERS" "$work/envs/organization/internal_users.yml"
kubectl kustomize "$work/envs/organization" >"$work/server.yaml"
python3 "$HERE/validate_server.py" "$work/server.yaml"
# The mounted audit ConfigMap must exist before the server rollout begins.
kubectl apply --dry-run=server -f "$GLOBAL/audit-rules.yaml" >/dev/null
kubectl apply --dry-run=server -f "$work/server.yaml" >/dev/null
kubectl apply --dry-run=server -f "$GLOBAL" >/dev/null
kubectl apply -f "$GLOBAL/audit-rules.yaml"
kubectl apply -f "$work/server.yaml"
kubectl apply -f "$GLOBAL"
kubectl -n "$NS" rollout status statefulset/wazuh-indexer --timeout=15m
kubectl -n "$NS" rollout status statefulset/wazuh-manager-master --timeout=15m
kubectl -n "$NS" rollout status deployment/wazuh-dashboard --timeout=15m

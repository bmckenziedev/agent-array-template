#!/usr/bin/env bash
set -euo pipefail
# Use an existing admitted task so verification cannot bypass Job admission.
if [[ $# -ne 4 ]]; then
  echo "Usage: $0 <kubeconfig> <session-jobs-namespace> <task-pod> <mirror-namespace>" >&2
  exit 2
fi
kubeconfig=$1
namespace=$2
pod=$3
mirror=$4
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
kubectl --kubeconfig "$kubeconfig" -n "$namespace" exec -i "$pod" -c tester --   env "MIRROR_NAMESPACE=$mirror" python3 - < "$here/probe_pip.py"
kubectl --kubeconfig "$kubeconfig" -n "$namespace" exec -i "$pod" -c tester --   env "MIRROR_NAMESPACE=$mirror" node - < "$here/probe_npm.js"
kubectl --kubeconfig "$kubeconfig" -n "$namespace" exec -i "$pod" -c tester --   env 'TARGETS=[["kubernetes.default.svc",443,"blocked"],["example.org",443,"blocked"]]'   node - < "$here/probe_tcp.js"

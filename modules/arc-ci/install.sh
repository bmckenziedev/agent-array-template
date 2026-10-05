#!/usr/bin/env bash
set -euo pipefail
if [[ ${1:-} != --yes ]]; then
  printf 'Preview: Helm controller and scale sets, explicit values, chart 0.15.0.\n'
  exit 0
fi
project=${3:?Provide the configured project name}
[[ "$project" =~ ^[a-z][a-z0-9-]*$ ]] || exit 2
values=${2:?Provide rendered/files/modules/arc-ci/helm directory}
if ! grep -Eq 'sha256:[0-9a-f]{64}"?$' "$values/controller/values.yaml"; then
  printf 'Refusing deploy: configure the verified ARC controller image digest.\n' >&2
  exit 2
fi
helm upgrade --install arc oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set-controller --version 0.15.0 --namespace "$project"-arc-systems -f "$values/controller/values.yaml"
helm upgrade --install arc-light oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set --version 0.15.0 --namespace "$project"-arc-runners -f "$values/light/values.yaml"
helm upgrade --install arc-heavy oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set --version 0.15.0 --namespace "$project"-arc-heavy -f "$values/heavy/values.yaml"

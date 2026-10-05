#!/usr/bin/env bash
set -euo pipefail
yes=false
[[ $# == 0 ]] || { [[ $# == 1 && $1 == --yes ]] || exit 2; yes=true; }
namespace=${ARGOCD_NAMESPACE:?set ARGOCD_NAMESPACE to the rendered namespace}
cmd=(helm upgrade --install argo-cd argo/argo-cd --version 10.9.6
  --namespace "$namespace" -f rendered/files/argocd/helm/argocd/values.yaml --wait --timeout 8m)
printf '%q ' "${cmd[@]}"; printf '\n'
if $yes; then "${cmd[@]}"; fi

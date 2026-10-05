#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
VALUES="${VALUES:-$HERE/../../rendered/files/platform/sealed-secrets/helm/sealed-secrets/values.yaml}"
CHART_VERSION=2.20.0
CHART_SHA256=6092d219c697f3c9ae3a386ba24bfdd86877c5e2fc8892e2af8a810145b990ef
if [[ "${1:-}" != --yes || $# != 1 ]]; then
  printf '%s
' "Plan: download and checksum chart $CHART_VERSION; helm upgrade --install sealed-secrets <verified-chart> --namespace kube-system -f $VALUES --wait --timeout 5m" "Run install.sh --yes to execute."
  exit 0
fi
[[ -r "$VALUES" ]] || { echo 'Render the complete values file first.' >&2; exit 1; }
grep -qF '0.40.0@sha256:b1ff382e9300dc9e74991f3177b18cafc06ce43b5c21349c86adbdd1d1c177d3' "$VALUES"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
helm repo add sealed-secrets https://bitnami.github.io/sealed-secrets
helm repo update sealed-secrets
helm pull sealed-secrets/sealed-secrets --version "$CHART_VERSION" -d "$tmp"
chart="$tmp/sealed-secrets-$CHART_VERSION.tgz"
printf '%s  %s
' "$CHART_SHA256" "$chart" | sha256sum -c -
printf 'helm upgrade --install sealed-secrets %q --namespace kube-system -f %q --wait --timeout 5m
' "$chart" "$VALUES"
helm upgrade --install sealed-secrets "$chart" --namespace kube-system -f "$VALUES" --wait --timeout 5m

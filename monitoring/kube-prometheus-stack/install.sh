#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
NS="${NS_MONITORING:-agent-array-monitoring}"
CHART_VERSION=91.8.2
BASE="$ROOT/rendered/files/monitoring/helm/kube-prometheus-stack/values.yaml"
ROUTING="$ROOT/rendered/files/monitoring/helm/kube-prometheus-stack/routing.values.yaml"
REVISION_FILE="${REVISION_FILE:-$ROOT/rendered/files/monitoring/kps.previous-revision}"
cmd=(helm upgrade --install kps prometheus-community/kube-prometheus-stack
  --namespace "$NS" --version "$CHART_VERSION" -f "$BASE" -f "$ROUTING"
  --post-renderer "$HERE/post_render.py" --wait --timeout 15m)
printf '%q ' "${cmd[@]}"; printf '\n'
if [[ $# == 0 ]]; then
  printf 'Review the rendered values; --yes is required to execute.\n'
  exit 0
fi
[[ $# == 1 && $1 == --yes ]] || { echo 'Usage: install.sh [--yes]' >&2; exit 64; }
[[ -f $BASE && -f $ROUTING ]] || { echo 'Render the organization first.' >&2; exit 1; }
umask 077
# helm list returns revision metadata only; no release values or Secret bodies.
helm list -n "$NS" --filter '^kps$' -o json |
  python3 -c 'import json,sys; rows=json.load(sys.stdin); print(rows[0]["revision"] if rows else 0)' >"$REVISION_FILE.tmp"
mv "$REVISION_FILE.tmp" "$REVISION_FILE"
"${cmd[@]}"

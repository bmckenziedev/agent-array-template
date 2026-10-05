#!/usr/bin/env bash
set -euo pipefail
root=${1:?Usage: validate_manifests.sh RENDERED [KUBERNETES_VERSION]}
version=${2:-${K8S_VERSION:-}}
# The renderer records the cluster version in rendered global data when available.
if [[ -z "$version" ]]; then
  version=$(python - "$root" <<'PY'
import pathlib, re, sys, yaml
for f in sorted((pathlib.Path(sys.argv[1]) / 'global').rglob('*.yaml')):
    for doc in yaml.safe_load_all(f.read_text()):
        if isinstance(doc, dict) and doc.get('kind') == 'ConfigMap':
            for key, value in doc.get('data', {}).items():
                if key.lower() in {'k8s_version', 'kubernetes_version'}:
                    print(str(value).lstrip('v').split('+')[0])
                    raise SystemExit
config = pathlib.Path('org/org.yaml')
if not config.exists():
    config = pathlib.Path('org/org.example.yaml')
print(str(yaml.safe_load(config.read_text())['cluster']['version']))
PY
)
fi
version=${version#v}
version=${version%%+*}
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
python - "$root" "$tmp" <<'PY'
import pathlib, sys, yaml
sys.path.insert(0, str(pathlib.Path('tools/ci').resolve()))
from rendered_assets import is_non_manifest
count = 0
for f in sorted(pathlib.Path(sys.argv[1]).rglob('*')):
    if f.suffix not in {'.yaml', '.yml'}:
        continue
    for doc in yaml.safe_load_all(f.read_text(encoding='utf-8')):
        if is_non_manifest(f.relative_to(pathlib.Path(sys.argv[1])).as_posix(), doc):
            continue
        if isinstance(doc, dict) and 'apiVersion' in doc and 'kind' in doc:
            count += 1
            pathlib.Path(sys.argv[2], f'{count:06d}.yaml').write_text(yaml.safe_dump(doc), encoding='utf-8')
print(f'Kubernetes documents: {count}')
if not count:
    raise SystemExit('No Kubernetes documents found')
PY
if command -v kubeconform >/dev/null; then
  schema_version=$(python - "$version" <<'PY'
import sys, urllib.error, urllib.request
version = sys.argv[1]
url = f'https://raw.githubusercontent.com/yannh/kubernetes-json-schema/master/v{version}-standalone-strict/pod-v1.json'
try:
    with urllib.request.urlopen(url, timeout=15):
        print(version)
except urllib.error.HTTPError as error:
    if error.code != 404:
        raise
    print('master')
PY
)
  result=0
  kubeconform -strict -summary -output json -ignore-missing-schemas -kubernetes-version "$schema_version" \
    -schema-location default \
    -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
    "$tmp" > "$tmp/summary.json" || result=$?
  cat "$tmp/summary.json"
  python - "$tmp/summary.json" <<'PY'
import json, sys
summary = json.load(open(sys.argv[1]))['summary']
if summary.get('valid', 0) == 0:
    raise SystemExit('Zero resources validated')
PY
  exit "$result"
else
  echo 'WARNING: kubeconform absent; client dry-run fallback is weaker (no schema validation).'
  command -v kubectl >/dev/null || { echo 'kubectl also absent' >&2; exit 1; }
  for file in "$tmp"/*.yaml; do
    kubectl apply --dry-run=client --validate=false -f "$file"
  done
fi

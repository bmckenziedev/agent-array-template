#!/usr/bin/env bash
set -euo pipefail
umask 077
# Local sealing uses the supplied public certificate; no SSH or live API call.
[[ $# == 5 ]] || { echo 'Usage: seal-alertmanager-urls.sh <namespace> <secret> <key> <certificate> <output>' >&2; exit 64; }
namespace=$1 secret=$2 key=$3 cert=$4 out=$5
[[ $namespace =~ ^[a-z0-9][a-z0-9-]*$ && $secret =~ ^[a-z0-9][a-z0-9.-]*$ && $key =~ ^[A-Za-z0-9_.-]+$ ]] || exit 64
[[ -r $cert ]] || { echo 'Public sealing certificate is required.' >&2; exit 1; }
value=''
if [[ -t 0 ]]; then
  read -rsp 'Receiver URL or key (hidden): ' value
  printf '\n' >&2
else
  IFS= read -r value || true
fi
[[ -n $value ]] || { echo 'Empty receiver value.' >&2; exit 1; }
work="$(mktemp -d)"
tmp="$work/sealed.json"
trap 'value=""; rm -rf "$work"' EXIT
printf '%s' "$value" >"$work/plaintext"
printf '%s' "$value" |
  kubectl create secret generic "$secret" -n "$namespace" --from-file="$key=/dev/stdin" --dry-run=client -o json |
  kubeseal --cert "$cert" --scope strict --format json >"$tmp"
# A failed or incorrectly configured sealer must never publish plaintext.
python3 - "$tmp" "$namespace" "$secret" "$key" "$work/plaintext" <<'PY'
import base64
import json
import sys
path, namespace, name, key, value_path = sys.argv[1:]
with open(value_path, encoding='utf-8') as stream:
    plaintext = stream.read()
with open(path, encoding='utf-8') as stream:
    result = json.load(stream)
assert result['kind'] == 'SealedSecret'
assert result['metadata']['name'] == name and result['metadata']['namespace'] == namespace
assert key in result['spec']['encryptedData']
assert 'data' not in result and 'stringData' not in result
assert not result['spec'].get('template', {}).get('data')
assert all(isinstance(v, str) and v.startswith('Ag') for v in result['spec']['encryptedData'].values())
assert all(v not in (plaintext, base64.b64encode(plaintext.encode()).decode())
           for v in result['spec']['encryptedData'].values())
PY
mv "$tmp" "$out"

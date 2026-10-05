#!/usr/bin/env bash
set -euo pipefail
[[ $# == 1 ]] || { echo 'Usage: validate-config.sh <alertmanager.yaml>' >&2; exit 64; }
if command -v amtool >/dev/null 2>&1; then
  amtool check-config "$1"
else
  python3 - "$1" <<'PY'
import sys
import yaml
config = yaml.safe_load(open(sys.argv[1], encoding='utf-8'))
names = {receiver['name'] for receiver in config['receivers']}
def check(route):
    assert route['receiver'] in names
    for child in route.get('routes', []):
        check(child)
check(config['route'])
print('PASS: YAML and receiver references; SKIP: amtool unavailable')
PY
fi

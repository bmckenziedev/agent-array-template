#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
for script in "$root"/[0-6][0-9]-*.sh; do
  bash "$script" | grep -q 'Preview only'
done
printf 'Maintenance window refusal: OK\n'

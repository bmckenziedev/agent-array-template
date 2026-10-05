#!/usr/bin/env bash
set -euo pipefail
yes=false
require=false
for arg in "$@"; do
  case "$arg" in
    --yes) yes=true ;;
    --require-encrypted) require=true ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done
root="${AA_FAKE_ROOT:-}{{LOGIN_HOST_ROOT}}"
probe="$root"
while [[ ! -e "$probe" ]]; do probe=$(dirname "$probe"); done
source=$(findmnt -n -o SOURCE --target "$probe")
source=${source%%\[*}
encrypted=false
if lsblk -s -n -o TYPE "$source" | grep -qx crypt; then encrypted=true; fi
printf 'Login root: %s; dm-crypt/LUKS ancestry: %s\n' "$root" "$encrypted"
if $require && ! $encrypted; then echo 'encrypted node volume required' >&2; exit 1; fi
printf 'install -d -o root -g root -m 0711 %q\n' "$root"
if $yes; then
  [[ $(id -u) == 0 ]] || { echo 'root required' >&2; exit 1; }
  [[ ! -L "$root" ]] || { echo 'symlink root refused' >&2; exit 1; }
  install -d -o root -g root -m 0711 "$root"
fi

#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
tmp=$(mktemp -d "$script_dir/.grow-test.XXXXXX")
case "$tmp" in
  "$script_dir"/.grow-test.*) ;;
  *) echo 'temporary directory escaped test scope' >&2; exit 1 ;;
esac
trap 'rm -rf "$tmp"' EXIT
stub_dir="$tmp/bin"
mkdir -p "$stub_dir"
log="$tmp/calls"

make_stub() {
  local name=$1
  shift
  printf '#!/usr/bin/env bash\n%s\n' "$*" >"$stub_dir/$name"
  chmod +x "$stub_dir/$name"
}

make_stub id 'echo "${FAKE_UID:-0}"'
make_stub findmnt 'echo "${FAKE_ROOT:-/dev/mapper/ubuntu--vg-ubuntu--lv ext4}"'
make_stub lvs 'if [[ "$*" == *"vg_name,lv_name"* ]]; then echo " ubuntu-vg ubuntu-lv"; else echo "LVS $*"; fi'
make_stub vgs 'if [[ "$*" == *"vg_free"* ]]; then echo "${FAKE_FREE:-107374182400}"; else echo "VGS $*"; fi'
make_stub df 'echo "DF $*"'
make_stub lvextend 'printf "%s\n" "$*" >>"$CALL_LOG"'

run() {
  PATH="$stub_dir:$PATH" CALL_LOG="$log" bash "$script_dir/../grow-root-lv.sh" "$@"
}

output=$(run)
grep -q 'DRY RUN: no changes made' <<<"$output"
[[ ! -e $log ]]
grep -q 'Extension: +106300440576B' <<<"$output"

output=$(run --yes --size 25G)
grep -q 'Extension: +25G' <<<"$output"
[[ $(<"$log") == '-r -L +25G /dev/ubuntu-vg/ubuntu-lv' ]]

rm "$log"
run --yes >/dev/null
[[ $(<"$log") == '-r -L +106300440576B /dev/ubuntu-vg/ubuntu-lv' ]]

if FAKE_ROOT='/dev/mapper/ubuntu--vg-ubuntu--lv xfs' run >/dev/null 2>&1; then
  echo "expected non-ext4 root to be rejected" >&2
  exit 1
fi
if FAKE_ROOT='/dev/mapper/other--vg-root ext4' run >/dev/null 2>&1; then
  echo "expected wrong root LV to be rejected" >&2
  exit 1
fi
if FAKE_UID=1000 run >/dev/null 2>&1; then
  echo "expected non-root execution to be rejected" >&2
  exit 1
fi
if FAKE_FREE=1073741824 run >/dev/null 2>&1; then
  echo "expected insufficient free space to be rejected" >&2
  exit 1
fi

echo "PASS: dry run, apply, target guards, root guard, and safety reserve"

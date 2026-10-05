#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir "$work/bin"
export SEAL_TEST_WORK="$work"
cat >"$work/bin/kubectl" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >"$SEAL_TEST_WORK/kubectl.argv"
[[ "$*" == *'create secret generic team-receiver'* && "$*" == *'--from-file=url=/dev/stdin'* ]]
value="$(cat)"
[[ $value == 'synthetic receiver value' ]]
printf '{"kind":"Secret","data":{"url":"mock"}}\n'
SH
cat >"$work/bin/kubeseal" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
cat >/dev/null
[[ ${SEAL_TEST_FAIL:-0} == 0 ]] || exit 1
if [[ ${SEAL_TEST_PLAIN:-0} == 1 ]]; then
  printf '{"kind":"Secret","data":{"url":"mock"}}\n'
else
  printf '{"kind":"SealedSecret","metadata":{"name":"team-receiver","namespace":"example-monitoring"},"spec":{"encryptedData":{"url":"AgSyntheticCiphertext"}}}\n'
fi
SH
chmod +x "$work/bin/"*
printf 'synthetic public certificate\n' >"$work/cert"
export PATH="$work/bin:$PATH"
printf 'synthetic receiver value\n' | bash "$HERE/../alertmanager/seal-alertmanager-urls.sh" example-monitoring team-receiver url "$work/cert" "$work/output" >"$work/log" 2>&1
[[ -s $work/output ]]
if grep -q 'synthetic receiver value' "$work/output" "$work/log" "$work/kubectl.argv"; then exit 1; fi
rm "$work/output"
if printf 'synthetic receiver value\n' | SEAL_TEST_FAIL=1 bash "$HERE/../alertmanager/seal-alertmanager-urls.sh" example-monitoring team-receiver url "$work/cert" "$work/output" >/dev/null 2>&1; then exit 1; fi
[[ ! -e $work/output ]]
if printf 'synthetic receiver value\n' | SEAL_TEST_PLAIN=1 bash "$HERE/../alertmanager/seal-alertmanager-urls.sh" example-monitoring team-receiver url "$work/cert" "$work/output" >/dev/null 2>&1; then exit 1; fi
[[ ! -e $work/output ]]
echo 'PASS: receiver Secret naming, no plaintext output/argv, fail-closed sealing'

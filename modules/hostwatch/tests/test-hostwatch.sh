#!/usr/bin/env bash
# Fake-environment test for hostwatch-collect.sh. It runs the REAL collector against stubbed
# commands and temp directories (reboot-required, /proc/mdstat, sysfs, k3s tree), with a PATH
# that holds only those stubs plus wrappers for the core tools, so "tool missing" really means
# command-not-found. Offline, touches nothing on the machine it runs on.
#
#   modules/hostwatch/test-hostwatch.sh
#
# Cases: healthy server, degraded host (+ the security-updates-since lifecycle), every tool
# missing, every tool failing, agent node, the tailscale peer trap, atomic write on failure,
# and a self-test proving the exposition validator is not vacuous.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
COLLECT="$(cd .. && pwd)/hostwatch-collect.sh"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
fails=0; passes=0

ok()   { passes=$((passes + 1)); }
fail() { fails=$((fails + 1)); printf 'FAIL [%s] %s\n' "${CASE:-?}" "$*" >&2; }

# --- exposition validator (strict, independent of the collector) --------------------------
# Every sample: name{labels} value, family declared by HELP then TYPE before its first sample,
# families contiguous, no repeated series, counters end in _total, gauges do not.
validate() {
  awk '
    function bad(m) { print "  " m > "/dev/stderr"; rc = 1 }
    /^# HELP / { name = $3; if (name in help) bad("duplicate HELP " name); help[name] = 1; pend = name; next }
    /^# TYPE / {
      name = $3
      if (name != pend) bad("TYPE " name " does not follow its HELP")
      if ($4 != "gauge" && $4 != "counter") bad("unexpected type " $4 " for " name)
      if ($4 == "counter" && name !~ /_total$/) bad("counter without _total: " name)
      if ($4 == "gauge" && name ~ /_total$/) bad("gauge with _total: " name)
      type[name] = $4; pend = ""; next
    }
    /^#/ { next }
    /^$/ { next }
    {
      line = $0
      sp = match(line, / -?[0-9]+(\.[0-9]+)?$/)
      if (!sp) { bad("not name{labels} value: " line); next }
      series = substr(line, 1, sp - 1)
      n = series; sub(/\{.*/, "", n)
      if (n !~ /^[a-zA-Z_:][a-zA-Z0-9_:]*$/) bad("bad metric name: " line)
      lab = substr(series, length(n) + 1)
      if (lab != "" && lab !~ /^\{[a-zA-Z_][a-zA-Z0-9_]*="([^"\\]|\\.)*"(,[a-zA-Z_][a-zA-Z0-9_]*="([^"\\]|\\.)*")*\}$/) bad("bad labels: " line)
      if (!(n in help) || !(n in type)) bad("sample before HELP/TYPE: " line)
      if (n != cur) { if (n in done) bad("family interleaved: " n); done[cur] = 1; cur = n }
      if (series in seen) bad("duplicate series: " series)
      seen[series] = 1
    }
    END { exit rc }
  ' "$1"
}

# --- fake environment ----------------------------------------------------------------------
CORE=(awk sed grep find mktemp mv chmod rm stat date tail cat env timeout mkdir touch)
REAL_PY=''
for p in python3 python; do
  c="$(command -v "$p" 2>/dev/null)" || continue
  if "$c" -c 'import json' >/dev/null 2>&1; then REAL_PY=$c; break; fi
done

stub() { { printf '#!/bin/bash\n'; cat; } >"$B/$1"; chmod +x "$B/$1"; }

new_case() {
  CASE=$1; C="$T/$1"; B="$C/bin"
  mkdir -p "$B" "$C/out" "$C/state" "$C/root/var/run" "$C/proc" "$C/sys/block" \
           "$C/k3s/server/tls/etcd" "$C/k3s/server/tls/a/b" "$C/k3s/agent/containerd/snap"
  local t real
  for t in "${CORE[@]}"; do
    real="$(command -v "$t")"
    printf '#!/bin/bash\nexec %q "$@"\n' "$real" >"$B/$t"; chmod +x "$B/$t"
  done
}

disk() { mkdir -p "$C/sys/block/$1/device"; }
cert() { printf 'notAfter=%s\n-----BEGIN CERTIFICATE-----\nCERT-CONTENT-MUST-NOT-LEAK\n' "$2" >"$1"; }

healthy_stubs() {
  stub apt-check <<'EOF'
echo "7;0" >&2
exit 1
EOF
  stub apt-config <<'EOF'
printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\nUnattended-Upgrade::Automatic-Reboot "false";\n'
EOF
  stub systemctl <<'EOF'
[[ "$*" == "is-active --quiet apt-daily-upgrade.timer" ]] && exit 0
exit 3
EOF
  stub timedatectl <<'EOF'
[[ "$*" == *NTPSynchronized* ]] && echo yes
EOF
  stub smartctl <<'EOF'
dev="${*: -1}"
printf '%s\n' "$*" >>"$FAKE/smartctl.log"
if grep -qx "$dev" "$FAKE/smart_fail" 2>/dev/null; then
  echo "SMART overall-health self-assessment test result: FAILED!"; exit 8
fi
echo "SMART overall-health self-assessment test result: PASSED"
EOF
  stub nvme <<'EOF'
used="$(cat "$FAKE/nvme_used" 2>/dev/null || echo 12)"
printf 'Smart Log for NVME device:%s namespace-id:ffffffff\ncritical_warning                        : 0\npercentage_used                         : %s%%\nmedia_errors                            : %s\nnum_err_log_entries                     : 9\n' "${*: -1}" "$used" "$(cat "$FAKE/nvme_errors" 2>/dev/null || echo 0)"
EOF
  stub openssl <<'EOF'
[[ "$1" == x509 ]] || exit 2
shift; f=''
while [[ $# -gt 0 ]]; do [[ "$1" == -in ]] && f="$2"; shift; done
grep -m1 '^notAfter=' "$f"
EOF
  stub tailscale <<'EOF'
cat "$FAKE/ts.json"
EOF
  [[ -z $REAL_PY ]] || printf '#!/bin/bash\nexec %q "$@"\n' "$REAL_PY" >"$B/python3"
  chmod +x "$B/python3" 2>/dev/null || true
  stub k3s <<'EOF'
printf 'KUBECONFIG=%s %s\n' "${KUBECONFIG:-}" "$*" >>"$FAKE/k3s.log"
[[ -f "$FAKE/k3s_fail" ]] && exit 1
cat "$FAKE/k3s_rows"
EOF
}

healthy_world() {
  disk sda; disk nvme0n1; mkdir -p "$C/sys/block/loop0" "$C/sys/block/md0"   # no device link: ignored
  printf 'Personalities : [raid1]\nmd0 : active raid1 nvme0n1p1[1] nvme1n1p1[0]\n      1046528 blocks super 1.2 [2/2] [UU]\n\nmd1 : active raid1 nvme1n1p2[0] nvme0n1p2[1]\n      1874191680 blocks super 1.2 [2/2] [UU]\n      bitmap: 3/14 pages [12KB], 65536KB chunk\n\nunused devices: <none>\n' >"$C/proc/mdstat"
  cert "$C/k3s/server/tls/client-admin.crt" 'Jan  2 03:04:05 2030 GMT'
  cert "$C/k3s/server/tls/etcd/server-ca.crt" 'Jan  2 03:04:05 2031 GMT'
  cert "$C/k3s/agent/client-kubelet.crt" 'Jan  2 03:04:05 2032 GMT'
  cert "$C/k3s/server/tls/a/b/too-deep.crt" 'Jan  2 03:04:05 2033 GMT'          # depth 3: ignored
  cert "$C/k3s/agent/containerd/snap/image-ca.crt" 'Jan  2 03:04:05 2034 GMT'   # container image: ignored
  printf '{"Self":{"HostName":"node-a","KeyExpiry":"2030-01-02T03:04:05Z"},"Peer":{"nodekey:a":{"KeyExpiry":"2031-02-03T04:05:06Z"}}}\n' >"$C/ts.json"
  printf 'agent-array\tghcr-pull\t2030-01-02T03:04:05Z\nagent-array\tplain\t\nmonitoring\tbad\tnext friday\nfoo\tbar\t2030-01-02\n' >"$C/k3s_rows"
  : >"$C/kubeconfig"
}

run_collect() {
  PATH="$B" FAKE="$C" \
    HOSTWATCH_TEXTFILE_DIR="$C/out" HOSTWATCH_STATE_DIR="$C/state" HOSTWATCH_ROOT="$C/root" \
    HOSTWATCH_PROC_ROOT="$C/proc" HOSTWATCH_SYS_ROOT="$C/sys" HOSTWATCH_K3S_ROOT="$C/k3s" \
    HOSTWATCH_KUBECONFIG="$C/kubeconfig" HOSTWATCH_APT_CHECK="${APT_CHECK_PATH:-$B/apt-check}" \
    HOSTWATCH_NOW="${NOW:-1700000000}" "$BASH" "$COLLECT"
}

PROM() { printf '%s' "$C/out/aa_hostwatch.prom"; }
has()  { if grep -Fxq -- "$1" "$(PROM)"; then ok; else fail "missing line: $1"; fi; }
lacks() { if grep -Fq -- "$1" "$(PROM)"; then fail "unexpected: $1"; else ok; fi; }
count() { local n; n="$(grep -c -- "$1" "$(PROM)" || true)"; if [[ $n == "$2" ]]; then ok; else fail "expected $2 lines matching '$1', got $n"; fi; }
valid() { if validate "$(PROM)"; then ok; else fail "exposition invalid"; fi; }
ts()   { date -u -d "$1" +%s; }
run_ok() { if run_collect >"$C/stdout" 2>"$C/stderr"; then ok; else fail "collector exited non-zero: $(cat "$C/stderr")"; fi; }
no_leak() {
  if grep -rq 'CERT-CONTENT-MUST-NOT-LEAK' "$C/out" "$C/stdout" "$C/stderr" "$C/state" 2>/dev/null; then fail "certificate content leaked"; else ok; fi
}

# --- case 1: healthy node-a-like server --------------------------------------------------------
new_case healthy; healthy_stubs; healthy_world
run_ok; valid; no_leak
has 'aa_hostwatch_collector_success 1'
has 'aa_hostwatch_last_success_timestamp_seconds 1700000000'
has 'aa_reboot_required 0'
has 'aa_reboot_required_since_timestamp_seconds 0'
has 'aa_security_updates_up 1'
has 'aa_security_updates_pending 0'
has 'aa_updates_pending 7'
has 'aa_security_updates_since_timestamp_seconds 0'
has 'aa_unattended_upgrades_up 1'
has 'aa_unattended_upgrades_enabled 1'
has 'aa_unattended_upgrades_auto_reboot 0'
has 'aa_unattended_upgrades_timer_active 1'
has 'aa_md_up 1'
has 'aa_md_degraded{device="md0"} 0'
has 'aa_md_degraded{device="md1"} 0'
has 'aa_md_resyncing{device="md1"} 0'
has 'aa_smart_up 1'
has 'aa_smart_healthy{device="/dev/sda"} 1'
has 'aa_smart_healthy{device="/dev/nvme0n1"} 1'
lacks 'loop0'
# Read-only, and a sleeping HDD is not woken: -n standby + -H for sda, plain -H for NVMe, nothing else.
if grep -qx -- '-n standby -H /dev/sda' "$C/smartctl.log" && grep -qx -- '-H /dev/nvme0n1' "$C/smartctl.log" \
   && [[ "$(wc -l <"$C/smartctl.log")" -eq 2 ]]; then ok; else fail "unexpected smartctl calls: $(cat "$C/smartctl.log")"; fi
has 'aa_nvme_up 1'
has 'aa_nvme_percentage_used{device="/dev/nvme0n1"} 12'
has 'aa_nvme_media_errors_total{device="/dev/nvme0n1"} 0'
count 'device="/dev/sda"} ' 1
has 'aa_time_sync_up 1'
has 'aa_time_synchronized 1'
has 'aa_k3s_certificates_up 1'
has "aa_k3s_certificate_not_after_timestamp_seconds{file=\"$C/k3s/server/tls/client-admin.crt\"} $(ts '2030-01-02T03:04:05Z')"
has "aa_k3s_certificate_not_after_timestamp_seconds{file=\"$C/k3s/server/tls/etcd/server-ca.crt\"} $(ts '2031-01-02T03:04:05Z')"
has "aa_k3s_certificate_not_after_timestamp_seconds{file=\"$C/k3s/agent/client-kubelet.crt\"} $(ts '2032-01-02T03:04:05Z')"
count 'k3s_certificate_not_after_timestamp_seconds{' 3          # the deep and container-image certs are not reported
lacks 'too-deep'; lacks 'image-ca'
has 'aa_credentials_up 1'
has "aa_credential_expiry_timestamp_seconds{secret_namespace=\"agent-array\",secret=\"ghcr-pull\"} $(ts '2030-01-02T03:04:05Z')"
has "aa_credential_expiry_timestamp_seconds{secret_namespace=\"foo\",secret=\"bar\"} $(ts '2030-01-02')"
has 'aa_credential_expiry_invalid{secret_namespace="monitoring",secret="bad"} 1'
lacks 'secret="plain"'                                         # no annotation: no series
# The Secret query is metadata-only: explicit jsonpath, never .data / -o yaml|json / describe.
if grep -q 'get secrets -A' "$C/k3s.log" && grep -Fq 'agent-array\.example\.org/expires-at' "$C/k3s.log" \
   && grep -q "KUBECONFIG=$C/kubeconfig" "$C/k3s.log" \
   && ! grep -Eq '\.data|-o (yaml|json)( |$)|describe|--show' "$C/k3s.log"; then ok; else fail "k3s kubectl was not a metadata-only jsonpath query: $(cat "$C/k3s.log")"; fi
if [[ -n $REAL_PY ]]; then
  has "aa_tailscale_up 1"
  has "aa_tailscale_key_expiry_timestamp_seconds $(ts '2030-01-02T03:04:05Z')"   # Self, not the peer's 2031
else
  echo 'SKIP tailscale parsing (no working python3 on this machine)'
fi
# World-readable (node-exporter runs as nobody) and no temp files left behind.
if [[ "$(stat -c %a "$(PROM)")" == 644 ]]; then ok; else fail "prom file mode is $(stat -c %a "$(PROM)")"; fi
if [[ -z "$(find "$C/out" -name '.aa_hostwatch.prom.*')" ]]; then ok; else fail "temp file left behind"; fi
# A second run overwrites the file in place (same output).
cp "$(PROM)" "$C/first.prom"; run_ok
if cmp -s "$C/first.prom" "$(PROM)"; then ok; else fail "second run differs from first"; fi
echo "PASS healthy"

# --- case 2: degraded host + security-updates-since lifecycle -------------------------------
new_case degraded; healthy_stubs; healthy_world
touch -d @1699000000 "$C/root/var/run/reboot-required"
printf 'libc6\nlinux-image-7.0.0-31-generic\nlinux-base\nlinux-image-7.0.0-34-generic\nlinux-base\n' >"$C/root/var/run/reboot-required.pkgs"
printf 'Personalities : [raid1]\nmd0 : active raid1 sda1[0] sdb1[2](F)\n      1046528 blocks super 1.2 [2/1] [U_]\n      [=>...................]  recovery =  8.8%% (92544/1046528) finish=1.0min\n\nmd1 : inactive snode-a[0](S)\n      1046528 blocks super 1.2\n\nmd2 : active raid1 sdd1[0] sde1[1]\n      1046528 blocks super 1.2 [2/2] [UU]\n      [>....................]  check =  0.4%% (4352/1046528) finish=9.0min\n\nunused devices: <none>\n' >"$C/proc/mdstat"
echo /dev/sda >"$C/smart_fail"; echo 85 >"$C/nvme_used"; echo 3 >"$C/nvme_errors"
stub apt-check <<'EOF'
echo "12;3" >&2
exit 1
EOF
stub apt-config <<'EOF'
printf 'APT::Periodic::Unattended-Upgrade "0";\nUnattended-Upgrade::Automatic-Reboot "true";\n'
EOF
stub systemctl <<'EOF'
exit 3
EOF
stub timedatectl <<'EOF'
echo no
EOF
run_ok; valid; no_leak
has 'aa_reboot_required 1'
has 'aa_reboot_required_since_timestamp_seconds 1699000000'
has 'aa_reboot_required_package{package="libc6"} 1'
has 'aa_reboot_required_package{package="linux-image-7.0.0-34-generic"} 1'
count 'aa_reboot_required_package{package="linux-base"}' 1   # duplicate in .pkgs: one series
has 'aa_security_updates_pending 3'
has 'aa_updates_pending 12'
has 'aa_security_updates_since_timestamp_seconds 1700000000'
has 'aa_unattended_upgrades_enabled 0'
has 'aa_unattended_upgrades_auto_reboot 1'
has 'aa_unattended_upgrades_timer_active 0'
has 'aa_md_degraded{device="md0"} 1'
has 'aa_md_resyncing{device="md0"} 1'
has 'aa_md_degraded{device="md1"} 1'
has 'aa_md_resyncing{device="md1"} 0'
has 'aa_md_degraded{device="md2"} 0'
has 'aa_md_resyncing{device="md2"} 1'                     # scheduled check: resyncing, not degraded
has 'aa_smart_healthy{device="/dev/sda"} 0'
has 'aa_smart_healthy{device="/dev/nvme0n1"} 1'
has 'aa_nvme_percentage_used{device="/dev/nvme0n1"} 85'
has 'aa_nvme_media_errors_total{device="/dev/nvme0n1"} 3'
has 'aa_time_sync_up 1'
has 'aa_time_synchronized 0'
# Two hours later the same updates are still pending: "since" must keep the first sighting.
NOW=1700007200 run_ok; valid
has 'aa_security_updates_since_timestamp_seconds 1700000000'
has 'aa_hostwatch_last_success_timestamp_seconds 1700007200'
# A garbage marker is replaced instead of producing an invalid sample.
echo 'not-a-number' >"$C/state/security-updates-since"
NOW=1700010000 run_ok; valid
has 'aa_security_updates_since_timestamp_seconds 1700010000'
# Updates applied: pending 0, since reset, marker removed.
stub apt-check <<'EOF'
echo "4;0" >&2
exit 1
EOF
NOW=1700020000 run_ok; valid
has 'aa_security_updates_pending 0'
has 'aa_security_updates_since_timestamp_seconds 0'
if [[ ! -e "$C/state/security-updates-since" ]]; then ok; else fail "marker not removed"; fi
echo "PASS degraded"

# --- case 3: every optional tool absent (hermetic PATH: command-not-found) ------------------
new_case missing-tools
disk sda; mkdir -p "$C/k3s/agent"
cert "$C/k3s/agent/client-kubelet.crt" 'Jan  2 03:04:05 2032 GMT'
APT_CHECK_PATH="$C/does-not-exist" run_ok; valid; no_leak
has 'aa_hostwatch_collector_success 1'
has 'aa_reboot_required 0'                              # file based: always works
has 'aa_security_updates_up 0'
has 'aa_unattended_upgrades_up 0'
lacks 'aa_md_'                                         # no /proc/mdstat: no md driver, check does not apply
has 'aa_smart_up 0'
has 'aa_nvme_up 0'
has 'aa_time_sync_up 0'
has 'aa_k3s_certificates_up 0'                         # k3s tree exists, openssl does not
has 'aa_tailscale_up 0'
lacks 'aa_credentials'                                 # no k3s binary: check does not apply
lacks 'aa_smart_healthy'
echo "PASS missing-tools"

# --- case 4: tools present but failing / printing garbage -----------------------------------
new_case failing-tools; healthy_stubs; healthy_world
rm -f "$C/proc/mdstat"; mkdir "$C/proc/mdstat"                  # exists but unreadable
stub apt-check <<'EOF'
echo "something went wrong" >&2
exit 1
EOF
: | stub apt-config
stub timedatectl <<'EOF'
echo "n/a"
EOF
stub smartctl <<'EOF'
echo "Smartctl open device: /dev/x failed: Operation not supported by device"
exit 2
EOF
: | stub nvme
stub openssl <<'EOF'
exit 1
EOF
echo 'this is not json' >"$C/ts.json"
touch "$C/k3s_fail"
run_ok; valid; no_leak
has 'aa_security_updates_up 0'
has 'aa_unattended_upgrades_up 0'
has 'aa_md_up 0'
lacks 'aa_md_degraded'
has 'aa_smart_up 0'
lacks 'aa_smart_healthy'
has 'aa_nvme_up 0'
has 'aa_time_sync_up 0'
lacks 'aa_time_synchronized'
has 'aa_k3s_certificates_up 0'
has 'aa_tailscale_up 0'
has 'aa_credentials_up 0'
lacks 'aa_credential_expiry'
has 'aa_hostwatch_collector_success 1'
echo "PASS failing-tools"

# --- case 5: agent node (k3s binary, no kubeconfig): credential check does not apply --------
new_case agent; healthy_stubs; healthy_world
rm -f "$C/kubeconfig"
rm -rf "$C/k3s/server"                  # a worker has no server/tls directory at all
run_ok; valid
lacks 'aa_credentials'
if [[ ! -e "$C/k3s.log" ]]; then ok; else fail "k3s was called on a node without a kubeconfig"; fi
has 'aa_k3s_certificates_up 1'
# The agent's own certificates are still found although the server/tls find fails first.
has "aa_k3s_certificate_not_after_timestamp_seconds{file=\"$C/k3s/agent/client-kubelet.crt\"} $(ts '2032-01-02T03:04:05Z')"
count 'k3s_certificate_not_after_timestamp_seconds{' 1
echo "PASS agent"

# --- case 6: tailscale peer trap + non-expiring keys (needs a real python3) -----------------
if [[ -n $REAL_PY ]]; then
  new_case tailscale; healthy_stubs; healthy_world
  # Tagged node: Self has no KeyExpiry but a peer does. Must be 0, never the peer's date.
  printf '{\n  "Self": {\n    "HostName": "tagged"\n  },\n  "Peer": {\n    "nodekey:a": {\n      "KeyExpiry": "2031-02-03T04:05:06Z"\n    }\n  }\n}\n' >"$C/ts.json"
  run_ok; valid
  has 'aa_tailscale_up 1'
  has 'aa_tailscale_key_expiry_timestamp_seconds 0'
  # Go zero time also means "no expiry".
  printf '{"Self":{"KeyExpiry":"0001-01-01T00:00:00Z"}}\n' >"$C/ts.json"
  run_ok
  has 'aa_tailscale_key_expiry_timestamp_seconds 0'
  # No Self at all (logged out): not a usable answer.
  printf '{"BackendState":"NeedsLogin"}\n' >"$C/ts.json"
  run_ok
  has 'aa_tailscale_up 0'
  lacks 'aa_tailscale_key_expiry'
  echo "PASS tailscale"
fi

# --- case 7: a failing run leaves the previous file untouched and no temp files -------------
new_case atomic; healthy_stubs; healthy_world
printf 'OLD\n' >"$C/out/aa_hostwatch.prom"
stub chmod <<'EOF'
exit 1
EOF
if run_collect >"$C/stdout" 2>"$C/stderr"; then fail "collector should fail when the final chmod fails"; else ok; fi
if [[ "$(cat "$(PROM)")" == OLD ]]; then ok; else fail "previous file was modified by a failed run"; fi
if [[ -z "$(find "$C/out" -name '.aa_hostwatch.prom.*')" ]]; then ok; else fail "failed run left a temp file"; fi
echo "PASS atomic"

# Missing timeout must fail closed before touching the previous exposition.
new_case missing-timeout; healthy_stubs; healthy_world
printf 'OLD\n' >"$C/out/aa_hostwatch.prom"
rm -f "$B/timeout"
if run_collect >"$C/stdout" 2>"$C/stderr"; then fail "collector ran without timeout"; else ok; fi
if [[ "$(cat "$(PROM)")" == OLD ]]; then ok; else fail "missing timeout replaced previous file"; fi
if grep -q 'missing required tool timeout' "$C/stderr"; then ok; else fail "missing timeout not diagnosed"; fi
if [[ -z "$(find "$C/out" -name '.aa_hostwatch.prom.*')" ]]; then ok; else fail "missing timeout left temporary output"; fi
echo "PASS missing-timeout"

# Assert every optional probe uses TERM plus a bounded KILL grace period.
new_case bounded-probes; healthy_stubs; healthy_world
REAL_TIMEOUT=$(command -v timeout)
stub timeout <<EOF
[[ \$1 == --kill-after=5s ]] || exit 99
printf '%s\n' "\$*" >>"\$FAKE/timeout.log"
exec "$REAL_TIMEOUT" "\$@"
EOF
run_ok; valid
if grep -q '15 systemctl is-active' "$C/timeout.log"; then ok; else fail "systemctl probe is unbounded"; fi
has 'aa_time_sync_up 1'
echo "PASS bounded-probes"

# --- case 8: the validator itself rejects broken expositions --------------------------------
CASE=validator; C="$T/validator"; mkdir -p "$C/out"
bad_cases=(
  'dup|# HELP a_up x\n# TYPE a_up gauge\na_up 1\na_up 1\n'
  'nohelp|a_up 1\n'
  'notype|# HELP a_up x\na_up 1\n'
  'interleave|# HELP a x\n# TYPE a gauge\n# HELP b x\n# TYPE b gauge\na{k="1"} 1\nb 1\na{k="2"} 1\n'
  'badvalue|# HELP a x\n# TYPE a gauge\na NaNx\n'
  'badlabel|# HELP a x\n# TYPE a gauge\na{k=1} 1\n'
  'counter|# HELP a x\n# TYPE a counter\na 1\n'
)
for entry in "${bad_cases[@]}"; do
  printf '%b' "${entry#*|}" >"$C/out/aa_hostwatch.prom"
  if validate "$(PROM)" 2>/dev/null; then fail "validator accepted broken exposition '${entry%%|*}'"; else ok; fi
done
printf '# HELP a_total x\n# TYPE a_total counter\na_total{k="1"} 1\n' >"$C/out/aa_hostwatch.prom"
if validate "$(PROM)"; then ok; else fail "validator rejected a good exposition"; fi
echo "PASS validator"

echo "---"
echo "$passes assertions passed, $fails failed"
[[ $fails -eq 0 ]]

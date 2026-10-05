#!/usr/bin/env bash
# hostwatch-collect.sh -- host + credential expiry watch for node_exporter's textfile collector.
#
# Read-only: it changes nothing on the host, applies no updates, never reboots anything and
# never reads Secret data (see check_credentials). README.md explains every metric.
#
# Rules this script follows:
#   * One .prom file, written atomically (temp file in the same directory, then rename), so
#     node_exporter never sees a half-written file.
#   * Every check is independent. A check that should work here but cannot (tool missing,
#     command failed) exports <check>_up 0 and all other checks still run. Checks that do not
#     apply to the host (no k3s, an agent without a kubeconfig, no /proc/mdstat because no md
#     arrays) export nothing at all.
#   * Series are written grouped by family, HELP/TYPE first, with duplicate series dropped, so
#     node_exporter's parser can never reject the whole file over one repeated line.
#
# Every path below can be overridden through HOSTWATCH_* variables; the fake-environment test
# (test-hostwatch.sh) uses that to run the real script against stubbed commands.
set -euo pipefail

TEXTFILE_DIR="${HOSTWATCH_TEXTFILE_DIR:-/var/lib/node_exporter/textfile_collector}"
STATE_DIR="${HOSTWATCH_STATE_DIR:-/var/lib/hostwatch}"
ROOT="${HOSTWATCH_ROOT:-}"                       # prefix for /var/run/reboot-required
PROC_ROOT="${HOSTWATCH_PROC_ROOT:-/proc}"
SYS_ROOT="${HOSTWATCH_SYS_ROOT:-/sys}"
K3S_ROOT="${HOSTWATCH_K3S_ROOT:-/var/lib/rancher/k3s}"
KUBECONFIG_FILE="${HOSTWATCH_KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
APT_CHECK="${HOSTWATCH_APT_CHECK:-/usr/lib/update-notifier/apt-check}"
NOW="${HOSTWATCH_NOW:-$(date +%s)}"
LABEL_PREFIX="${HOSTWATCH_LABEL_PREFIX:-agent-array.example.org}"
PROM_FILE="$TEXTFILE_DIR/aa_hostwatch.prom"

# Never run an optional probe unbounded. Fail before replacing the last good file
# if the core runtime is incomplete; stale data remains visible to alerting.
for tool in timeout awk sed grep find mktemp mv chmod rm stat date tail cat env mkdir; do
  command -v "$tool" >/dev/null 2>&1 || { printf 'hostwatch: missing required tool %s\n' "$tool" >&2; exit 1; }
done
[[ $NOW =~ ^[0-9]+$ ]] || { printf 'hostwatch: invalid timestamp\n' >&2; exit 1; }

mkdir -p "$TEXTFILE_DIR" "$STATE_DIR"
tmp="$(mktemp "$TEXTFILE_DIR/.aa_hostwatch.prom.XXXXXX")"
trap 'rm -f "$tmp"' EXIT

ORDER=()           # family names in output order
declare -A HELP TYPE
SAMPLES=()         # "name<TAB>{labels}<TAB>value"

# fam NAME TYPE HELP -- declare a metric family (printed only if it ends up with samples).
fam() { ORDER+=("$1"); TYPE[$1]="$2"; HELP[$1]="$3"; }

# sample NAME LABELS VALUE -- LABELS is '' or '{k="v",...}'. Non-numeric values are dropped so
# the file always stays well formed, whatever a parsed command printed.
sample() {
  [[ $3 =~ ^-?[0-9]+(\.[0-9]+)?$ ]] || return 0
  SAMPLES+=("$(printf '%s\t%s\t%s' "$1" "$2" "$3")")
}

# esc VALUE -- escape a label value for the text format.
esc() {
  local s=$1
  s=${s//\\/\\\\}; s=${s//\"/\\\"}; s=${s//$'\n'/\\n}; s=${s//$'\t'/ }; s=${s//$'\r'/}
  printf '%s' "$s"
}

have() { command -v "$1" >/dev/null 2>&1; }
# tmo SECONDS CMD... -- bound a command so a hung tool cannot stall the whole run.
tmo() { local t=$1; shift; timeout --kill-after=5s "$t" "$@"; }
# epoch DATE -- unix time of a date string, or non-zero.
epoch() { date -u -d "$1" +%s 2>/dev/null; }

# ---------------------------------------------------------------------------------------------
# Metric families
# ---------------------------------------------------------------------------------------------
fam aa_hostwatch_collector_success gauge 'Whether the latest hostwatch collection run completed (always 1 in a written file).'
fam aa_hostwatch_last_success_timestamp_seconds gauge 'Unix time of the latest completed hostwatch collection run.'

fam aa_reboot_required gauge 'Whether /var/run/reboot-required exists (1) or not (0).'
fam aa_reboot_required_since_timestamp_seconds gauge 'mtime of /var/run/reboot-required (when the reboot became necessary), 0 when no reboot is pending.'
fam aa_reboot_required_package gauge 'Packages listed in /var/run/reboot-required.pkgs (value is always 1).'

fam aa_security_updates_up gauge '1 if the pending-update count could be read (apt-check), 0 if it should work here but did not.'
fam aa_security_updates_pending gauge 'Pending security updates reported by apt-check.'
fam aa_updates_pending gauge 'All pending package updates reported by apt-check (security or not).'
fam aa_security_updates_since_timestamp_seconds gauge 'When hostwatch first saw security updates pending (lower bound; 0 when none are pending).'

fam aa_unattended_upgrades_up gauge '1 if the apt configuration could be read, 0 otherwise.'
fam aa_unattended_upgrades_enabled gauge '1 if APT::Periodic::Unattended-Upgrade is a non-zero interval.'
fam aa_unattended_upgrades_auto_reboot gauge '1 if Unattended-Upgrade::Automatic-Reboot is true (hostwatch never sets it).'
fam aa_unattended_upgrades_timer_active gauge '1 if apt-daily-upgrade.timer is active (the timer that actually runs unattended upgrades).'

fam aa_md_up gauge '1 if /proc/mdstat could be read, 0 if it exists but could not be read. Hosts without md arrays (no /proc/mdstat) export no md series.'
fam aa_md_degraded gauge '1 if an md array is inactive or has fewer active than configured members.'
fam aa_md_resyncing gauge '1 if an md array is recovering, resyncing, reshaping or being checked.'

fam aa_smart_up gauge '1 if smartctl gave a health verdict for at least one disk, 0 if smartctl is missing or answered for none.'
fam aa_smart_healthy gauge 'SMART overall health per disk: 1 PASSED/OK, 0 FAILED. Disks without a verdict export nothing.'
fam aa_nvme_up gauge '1 if nvme-cli could read every NVMe disk (or there is none), 0 if nvme is missing or a read failed.'
fam aa_nvme_percentage_used gauge 'NVMe endurance used, percentage_used from the SMART log (can exceed 100).'
fam aa_nvme_media_errors_total counter 'NVMe media and data integrity errors from the SMART log.'

fam aa_time_sync_up gauge '1 if the clock synchronisation state could be read, 0 otherwise.'
fam aa_time_synchronized gauge '1 if the kernel clock is synchronised (timedatectl NTPSynchronized).'

fam aa_k3s_certificates_up gauge '1 if k3s certificate expiry could be read, 0 if it should work here but did not.'
fam aa_k3s_certificate_not_after_timestamp_seconds gauge 'notAfter of each k3s certificate file (path only, never contents).'

fam aa_tailscale_up gauge '1 if tailscale status could be read, 0 otherwise.'
fam aa_tailscale_key_expiry_timestamp_seconds gauge 'Tailscale node key expiry (Self.KeyExpiry); 0 means the key does not expire (tagged nodes).'

fam aa_credentials_up gauge '1 if Secret expiry annotations could be listed (API servers only), 0 otherwise.'
fam aa_credential_expiry_timestamp_seconds gauge 'Expiry from the agent-array/expires-at annotation of a Secret.'
fam aa_credential_expiry_invalid gauge 'A Secret carries an agent-array/expires-at annotation that is not an RFC3339 date (value is always 1).'

# ---------------------------------------------------------------------------------------------
# Checks. Each ends with an explicit "return 0" on its normal paths; run_check turns an
# unexpected non-zero return into <check>_up 0.
# ---------------------------------------------------------------------------------------------

# Reboot state is file based and needs no optional tool.
check_reboot() {
  local f="$ROOT/var/run/reboot-required" since pkg
  if [[ -e $f ]]; then
    # If the mtime cannot be read, claim "since now": never invent an old, alarming date.
    since="$(stat -c %Y "$f" 2>/dev/null)" || since=$NOW
    sample aa_reboot_required '' 1
    sample aa_reboot_required_since_timestamp_seconds '' "$since"
    if [[ -r $f.pkgs ]]; then
      while IFS= read -r pkg || [[ -n $pkg ]]; do
        pkg=${pkg%$'\r'}
        if [[ -n $pkg ]]; then sample aa_reboot_required_package "{package=\"$(esc "$pkg")\"}" 1; fi
      done <"$f.pkgs"
    fi
  else
    sample aa_reboot_required '' 0
    sample aa_reboot_required_since_timestamp_seconds '' 0
  fi
  return 0
}

# apt-check prints "<all>;<security>" on stderr and exits non-zero on purpose.
check_security_updates() {
  local out line total='' security='' first='' marker="$STATE_DIR/security-updates-since"
  if [[ -x $APT_CHECK ]]; then
    out="$(tmo 120 "$APT_CHECK" 2>&1 || true)"
    line="$(grep -E '^[0-9]+;[0-9]+$' <<<"$out" | tail -n 1 || true)"
    if [[ $line =~ ^([0-9]+)\;([0-9]+)$ ]]; then total=${BASH_REMATCH[1]}; security=${BASH_REMATCH[2]}; fi
  fi
  if [[ -z $security ]]; then
    sample aa_security_updates_up '' 0
    return 0
  fi
  sample aa_security_updates_up '' 1
  sample aa_security_updates_pending '' "$security"
  sample aa_updates_pending '' "$total"
  if (( security > 0 )); then
    # Remember when security updates were first seen pending; reset once none are pending.
    if [[ -r $marker ]]; then read -r first <"$marker" || true; fi
    if ! [[ $first =~ ^[0-9]+$ ]]; then
      first=$NOW
      printf '%s\n' "$first" >"$marker.tmp.$$" && mv -f "$marker.tmp.$$" "$marker"
    fi
  else
    rm -f "$marker"
    first=0
  fi
  sample aa_security_updates_since_timestamp_seconds '' "$first"
  return 0
}

# Report the unattended-upgrades configuration; never change it.
check_unattended_upgrades() {
  local cfg v
  if ! have apt-config || ! cfg="$(tmo 30 apt-config dump 2>/dev/null)" || [[ -z $cfg ]]; then
    sample aa_unattended_upgrades_up '' 0
    return 0
  fi
  cfg_val() { awk -v k="$1" '$1 == k { gsub(/[";]/, "", $2); print $2; exit }' <<<"$cfg"; }
  sample aa_unattended_upgrades_up '' 1
  v="$(cfg_val 'APT::Periodic::Unattended-Upgrade')"
  if [[ $v =~ ^[1-9][0-9]*$ ]]; then sample aa_unattended_upgrades_enabled '' 1; else sample aa_unattended_upgrades_enabled '' 0; fi
  v="$(cfg_val 'Unattended-Upgrade::Automatic-Reboot')"
  if [[ ${v,,} == true ]]; then sample aa_unattended_upgrades_auto_reboot '' 1; else sample aa_unattended_upgrades_auto_reboot '' 0; fi
  if have systemctl; then
    if tmo 15 systemctl is-active --quiet apt-daily-upgrade.timer 2>/dev/null; then sample aa_unattended_upgrades_timer_active '' 1; else sample aa_unattended_upgrades_timer_active '' 0; fi
  fi
  return 0
}

check_md() {
  local f="$PROC_ROOT/mdstat" dev state degraded resync content
  local re_missing='\[[U_]*_[U_]*\]' re_count='\[([0-9]+)/([0-9]+)\]' re_sync='(recovery|resync|reshape|check)[[:space:]]*='
  # No /proc/mdstat: the md driver is not loaded, so there are no arrays that could be degraded.
  if [[ ! -e $f ]]; then return 0; fi
  if ! content="$(cat "$f" 2>/dev/null)"; then
    sample aa_md_up '' 0
    return 0
  fi
  sample aa_md_up '' 1
  # Join each array's continuation lines onto its "mdN : ..." header line.
  while IFS=$'\t' read -r dev state; do
    [[ -n $dev ]] || continue
    degraded=0; resync=0
    [[ $state == *": inactive"* ]] && degraded=1
    [[ $state =~ $re_missing ]] && degraded=1
    if [[ $state =~ $re_count ]] && (( BASH_REMATCH[1] < BASH_REMATCH[2] )); then degraded=1; fi
    [[ $state =~ $re_sync ]] && resync=1
    sample aa_md_degraded "{device=\"$(esc "$dev")\"}" "$degraded"
    sample aa_md_resyncing "{device=\"$(esc "$dev")\"}" "$resync"
  done < <(awk '/^md[0-9]+[[:space:]]*:/ { if (d != "") print d "\t" s; d = $1; s = $0; next } d != "" { s = s " " $0 } END { if (d != "") print d "\t" s }' <<<"$content")
  return 0
}

# Real block devices only (sysfs lists loop/zram/md too, but they have no "device" link).
list_disks() {
  local d n
  for d in "$SYS_ROOT"/block/*; do
    n=${d##*/}
    case $n in
      sd[a-z]*|hd[a-z]*|vd[a-z]*|nvme[0-9]*n[0-9]*) if [[ -e $d/device ]]; then printf '%s\n' "$n"; fi ;;
    esac
  done
  return 0
}

# SMART health queries are read-only and do not initiate device tests.
check_smart() {
  local disk out verdict seen=0 answered=0 nocheck
  if ! have smartctl; then
    sample aa_smart_up '' 0
    return 0
  fi
  while read -r disk; do
    [[ -n $disk ]] || continue
    seen=1
    # -n standby: a sleeping HDD stays asleep (it just gives no verdict this run). NVMe has no standby.
    nocheck=(); [[ $disk == nvme* ]] || nocheck=(-n standby)
    out="$(tmo 60 smartctl "${nocheck[@]}" -H "/dev/$disk" 2>/dev/null || true)"
    if grep -Eqi 'overall-health[^:]*:[[:space:]]*PASSED|SMART Health Status:[[:space:]]*OK' <<<"$out"; then verdict=1
    elif grep -Eqi 'overall-health[^:]*:[[:space:]]*FAILED|SMART Health Status:' <<<"$out"; then verdict=0
    else continue   # no verdict (virtual disk, USB bridge, open error): not "failing", just unknown
    fi
    answered=1
    sample aa_smart_healthy "{device=\"/dev/$(esc "$disk")\"}" "$verdict"
  done < <(list_disks)
  if (( seen && ! answered )); then sample aa_smart_up '' 0; else sample aa_smart_up '' 1; fi
  return 0
}

check_nvme() {
  local disk out used errors failed=0
  if ! have nvme; then
    sample aa_nvme_up '' 0
    return 0
  fi
  while read -r disk; do
    [[ $disk == nvme* ]] || continue
    out="$(tmo 60 nvme smart-log "/dev/$disk" 2>/dev/null || true)"
    used="$(awk -F: '/^percentage_used/ { gsub(/[% \t]/, "", $2); print $2; exit }' <<<"$out")"
    errors="$(awk -F: '/^media_errors/ { gsub(/[ ,\t]/, "", $2); print $2; exit }' <<<"$out")"
    if [[ $used =~ ^[0-9]+$ ]]; then sample aa_nvme_percentage_used "{device=\"/dev/$(esc "$disk")\"}" "$used"; else failed=1; fi
    if [[ $errors =~ ^[0-9]+$ ]]; then sample aa_nvme_media_errors_total "{device=\"/dev/$(esc "$disk")\"}" "$errors"; else failed=1; fi
  done < <(list_disks)
  sample aa_nvme_up '' $(( ! failed ))
  return 0
}

check_time_sync() {
  local synced=''
  if have timedatectl; then synced="$(tmo 15 timedatectl show -p NTPSynchronized --value 2>/dev/null || true)"; fi
  case $synced in
    yes) sample aa_time_sync_up '' 1; sample aa_time_synchronized '' 1 ;;
    no)  sample aa_time_sync_up '' 1; sample aa_time_synchronized '' 0 ;;
    *)   sample aa_time_sync_up '' 0 ;;
  esac
  return 0
}

# notAfter only, per file; certificate contents never leave openssl. The find is depth-limited on
# purpose: unpacked container images under agent/containerd hold hundreds of *.crt files.
check_certs() {
  local cert end ts n=0
  if [[ ! -d $K3S_ROOT/server/tls && ! -d $K3S_ROOT/agent ]]; then return 0; fi   # not a k3s host
  if ! have openssl; then
    sample aa_k3s_certificates_up '' 0
    return 0
  fi
  while IFS= read -r -d '' cert; do
    end="$(tmo 10 openssl x509 -in "$cert" -noout -enddate 2>/dev/null | sed -n 's/^notAfter=//p' || true)"
    if [[ -n $end ]] && ts="$(epoch "$end")"; then
      sample aa_k3s_certificate_not_after_timestamp_seconds "{file=\"$(esc "$cert")\"}" "$ts"
      n=$((n + 1))
    fi
  # "|| true": this runs in a subshell with errexit on, and an agent has no server/tls directory,
  # so the first find fails and would otherwise end the subshell before the agent's own find runs.
  done < <({ find "$K3S_ROOT/server/tls" -maxdepth 2 -type f -name '*.crt' -print0 || true
             find "$K3S_ROOT/agent" -maxdepth 1 -type f -name '*.crt' -print0 || true; } 2>/dev/null)
  sample aa_k3s_certificates_up '' $(( n > 0 ? 1 : 0 ))
  return 0
}

# Self.KeyExpiry only. Peers have their own KeyExpiry fields, so this parses the JSON properly
# instead of grepping for the first one.
check_tailscale() {
  local status expiry ts
  if ! have tailscale || ! have python3 || ! status="$(tmo 20 tailscale status --json 2>/dev/null)" || [[ -z $status ]]; then
    sample aa_tailscale_up '' 0
    return 0
  fi
  if ! expiry="$(tmo 10 python3 -c '
import json, sys
self = json.load(sys.stdin).get("Self")
if not isinstance(self, dict):
    sys.exit(3)
print(self.get("KeyExpiry") or "")' <<<"$status" 2>/dev/null)"; then
    sample aa_tailscale_up '' 0
    return 0
  fi
  sample aa_tailscale_up '' 1
  ts=0
  if [[ -n $expiry && $expiry != 0001-01-01T* ]]; then ts="$(epoch "$expiry")" || ts=0; fi
  sample aa_tailscale_key_expiry_timestamp_seconds '' "$ts"
  return 0
}

# Credential expiry from the agent-array/expires-at annotation on Secrets (set by
# the organization credential provisioning flow). The jsonpath names namespace, name and that one
# annotation, so its command output contains only those three fields. Client-side JSONPath
# does not restrict API authorization; the host identity can still list Secret bodies.
# No Secret data is printed or stored. An agent normally has no kubeconfig,
# so there the check does not apply and exports nothing.
check_credentials() {
  local rows ns name value ts
  local re_date='^[0-9]{4}-[0-9]{2}-[0-9]{2}([Tt ][0-9:.]+([Zz]|[+-][0-9:]+)?)?$'
  if ! have k3s || [[ ! -r $KUBECONFIG_FILE ]]; then return 0; fi
  if ! rows="$(tmo 90 env KUBECONFIG="$KUBECONFIG_FILE" k3s kubectl get secrets -A --request-timeout=60s \
      -o "jsonpath={range .items[*]}{.metadata.namespace}{\"\t\"}{.metadata.name}{\"\t\"}{.metadata.annotations['${LABEL_PREFIX//./\\.}/expires-at']}{\"\n\"}{end}" 2>/dev/null)"; then
    sample aa_credentials_up '' 0
    return 0
  fi
  sample aa_credentials_up '' 1
  while IFS=$'\t' read -r ns name value; do
    [[ -n $ns && -n ${value:-} ]] || continue
    if [[ $value =~ $re_date ]] && ts="$(epoch "$value")"; then
      sample aa_credential_expiry_timestamp_seconds "{secret_namespace=\"$(esc "$ns")\",secret=\"$(esc "$name")\"}" "$ts"
    else
      # A typo in the annotation must not silently switch the expiry watch off for that Secret.
      sample aa_credential_expiry_invalid "{secret_namespace=\"$(esc "$ns")\",secret=\"$(esc "$name")\"}" 1
    fi
  done <<<"$rows"
  return 0
}

# run_check FUNCTION [UP_METRIC] -- never let one check abort the run.
run_check() {
  if ! "$1"; then
    if [[ -n ${2:-} ]]; then sample "$2" '' 0; fi
  fi
}

run_check check_reboot
run_check check_security_updates aa_security_updates_up
run_check check_unattended_upgrades aa_unattended_upgrades_up
run_check check_md aa_md_up
run_check check_smart aa_smart_up
run_check check_nvme aa_nvme_up
run_check check_time_sync aa_time_sync_up
run_check check_certs aa_k3s_certificates_up
run_check check_tailscale aa_tailscale_up
run_check check_credentials aa_credentials_up

sample aa_hostwatch_collector_success '' 1
sample aa_hostwatch_last_success_timestamp_seconds '' "$NOW"

# Assemble: per family, HELP/TYPE then its series (first occurrence wins on duplicates).
for name in "${ORDER[@]}"; do
  body="$(printf '%s\n' "${SAMPLES[@]}" | awk -F'\t' -v n="$name" '$1 == n && !seen[$2]++ { print $1 $2 " " $3 }')"
  [[ -n $body ]] || continue
  printf '# HELP %s %s\n# TYPE %s %s\n%s\n' "$name" "${HELP[$name]}" "$name" "${TYPE[$name]}" "$body" >>"$tmp"
done

chmod 0644 "$tmp"
mv -f "$tmp" "$PROM_FILE"
trap - EXIT

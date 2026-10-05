#!/usr/bin/env bash
# kata-gc.sh -- remove the per-sandbox state that Kata 3.32 leaves in /run after a pod is
# gone (README "Garbage collector" and "Known issues"): /run/vc/sbs/<id> (persist.json +
# shim-monitor.sock) and /run/kata-containers/shared/sandboxes/<id> (an empty
# mounts/private/shared skeleton). A few KB of tmpfs per pod, but every CI job adds a pair
# and only a reboot clears them.
#
# Installed on each Kata node as /usr/local/sbin/kata-gc.sh (source of truth: platform/kata/ in
# the agent-array repo; install-kata-gc.sh copies it) and driven hourly by kata-gc.timer.
# Run as root ON THE NODE.
#
#   kata-gc.sh --yes       remove stale state, write the textfile metrics
#   kata-gc.sh --dry-run   list what would be removed; change nothing, write no metrics
#
# A directory is removed ONLY when every one of these holds:
#   1. it sits directly under one of the two roots, is a real directory (not a symlink) and
#      its name is a 64-hex sandbox id. Anything else is counted as "other" and left alone.
#   2. containerd does not know the sandbox: not listed by `crictl pods` (any state: a deleted
#      pod's sandbox stays NotReady until the kubelet garbage-collects it), not the
#      podSandboxId of any container in `crictl ps -a`, not in `ctr sandboxes ls`.
#   3. no kata process carries the id: a containerd-shim-kata* process, or the VMM / virtiofsd
#      it started, read from <proc>/<pid>/cmdline. Only argv[0] decides whether a process is
#      looked at, so no `pgrep -f` pattern can match an unrelated command line (or this one).
#   4. the sandbox's own files show no owner: a *.sock whose path is still in the kernel's
#      unix socket table (a live listener), or a pid file whose pid is alive. A pid file that
#      cannot be read as a number counts as "owned": when unsure, keep. File contents are
#      never read (persist.json holds the pod spec, including env values).
#   5. nothing is mounted at or below it in the host mount namespace (never rm -rf through a
#      bind mount). Live sandboxes keep their mounts in the shim's private namespace, so this
#      is a last-line check, not the primary proof.
#   6. it was last modified more than KATA_GC_MIN_AGE_SEC ago (default 600). The inventory is
#      a snapshot; the age guard covers a sandbox being created or torn down while this runs.
#
# One verdict per sandbox id: the sbs and shared directories of an id are removed together or
# kept together (a young or mounted half keeps the other half). Order matters: the directories
# are listed BEFORE the inventory is taken, so every candidate already existed when containerd
# and the process table were read. A sandbox created later is not a candidate at all.
#
# If an inventory source fails or is implausible (containerd lists no sandbox at all while
# state directories exist), the run aborts and removes nothing: an empty list would otherwise
# look like "nothing is live". Each decision errs towards keeping.
#
# Output: counts only (plus the paths in --dry-run), never file contents. Metrics:
# $TEXTFILE_DIR/aa_kata_gc.prom, read by node-exporter's textfile collector
# (monitoring/kube-prometheus-stack/values.yaml), written atomically.
#
# Test hooks (platform/kata/tests/test-kata-gc.sh): KATA_GC_SBS_ROOT, KATA_GC_SHARED_ROOT,
# KATA_GC_PROC (a fake /proc), KATA_GC_LOCK, KATA_GC_NODE, CRICTL, CTR, TEXTFILE_DIR.
# Runtime-rs cleanup is unsupported. Refuse unknown layouts instead of guessing roots.
# KATA_GC_CONFIG selects the non-secret containerd Kata drop-in for offline fixtures.
set -euo pipefail

SBS_ROOT=${KATA_GC_SBS_ROOT:-/run/vc/sbs}
SHARED_ROOT=${KATA_GC_SHARED_ROOT:-/run/kata-containers/shared/sandboxes}
MIN_AGE=${KATA_GC_MIN_AGE_SEC:-600}
PROC=${KATA_GC_PROC:-/proc}
LOCK_FILE=${KATA_GC_LOCK:-/run/kata-gc.lock}
TEXTFILE_DIR=${TEXTFILE_DIR:-/var/lib/node_exporter/textfile_collector}
PROM_FILE=aa_kata_gc.prom
# k3s bundles crictl and ctr; CRICTL/CTR may be overridden for a non-k3s containerd.
read -r -a CRICTL <<<"${CRICTL:-k3s crictl}"
read -r -a CTR    <<<"${CTR:-k3s ctr -n k8s.io}"
# Set KATA_GC_NODE to the inventory node name to join kube_* metrics.
NODE=${KATA_GC_NODE:-$(hostname | tr '[:upper:]' '[:lower:]')}

usage() {
  printf 'usage: %s [--dry-run|--yes]\n' "${0##*/}"
  printf '  removes stale Kata per-sandbox state that no containerd sandbox or kata process owns\n'
  printf '  --dry-run   list what would be removed; change nothing, write no metrics\n'
}

DRY_RUN=1
case ${1:-} in
  ""|--dry-run|-n) ;;
  --yes) DRY_RUN=0 ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 64 ;;
esac
(( $# <= 1 )) || { usage >&2; exit 64; }

log()  { printf '[kata-gc] %s\n' "$*"; }
warn() { printf '[kata-gc] WARNING: %s\n' "$*" >&2; }
die()  { printf '[kata-gc] ERROR: %s\n' "$*" >&2; exit 1; }

[[ $MIN_AGE =~ ^[0-9]+$ ]] || die "KATA_GC_MIN_AGE_SEC must be a whole number of seconds"
[[ $(id -u) -eq 0 ]] || die "run as root"
command -v flock >/dev/null 2>&1 || die "flock not found"
# Taken before the metric trap below: a run that loses the race must not overwrite the
# metrics of the run that holds the lock.
if (( ! DRY_RUN )); then
  exec 9>"$LOCK_FILE"
  flock -n 9 || die "another kata-gc run holds $LOCK_FILE"
fi

# ---- counters --------------------------------------------------------------------------
# n[<root>:<kind>]. Initialised before anything can fail, so the EXIT trap can always write
# a full metric set.
ROOTS=(sbs shared)
KEPT_KINDS=(live state young mounted other)
declare -A n=()
tally() { n[$1:$2]=$(( ${n[$1:$2]:-0} + 1 )); }
got()   { printf '%s' "${n[$1:$2]:-0}"; }
n_shims=0 n_known=0 inv_err=0
START=$(date +%s)
RC=1   # pessimistic until the run completes

# ---- textfile metrics ------------------------------------------------------------------
prom_write() {  # metric lines on stdin -> $TEXTFILE_DIR/$PROM_FILE, atomically, 0644
  local f=$TEXTFILE_DIR/$PROM_FILE
  # umask 022 so a missing parent is traversable by node-exporter (runs as nobody).
  [[ -d $TEXTFILE_DIR ]] || (umask 022 && mkdir -p "$TEXTFILE_DIR") || { cat >/dev/null; return 1; }
  cat >"$f.tmp.$$" && chmod 0644 "$f.tmp.$$" && mv -f "$f.tmp.$$" "$f"
}
prom_prev() {   # $1 = metric with labels -> its previous value in the file, or 0
  local v
  v=$(awk -v m="$1" '$1==m {v=$2} END {if (v!="") print v}' "$TEXTFILE_DIR/$PROM_FILE" 2>/dev/null) || true
  printf '%s\n' "${v:-0}"
}
head_() { printf '# HELP %s %s\n# TYPE %s %s\n' "$1" "$2" "$1" "${3:-gauge}"; }
on_exit() {
  local rc=$? now r k tot
  if (( ! DRY_RUN )); then
    rm -f -- "$TEXTFILE_DIR/$PROM_FILE.tmp.$$" 2>/dev/null || true
  fi
  # die exits non-zero; a completed run sets RC=0 before exiting.
  (( rc == 0 )) || RC=$rc
  if (( DRY_RUN )); then exit "$RC"; fi
  now=$(date +%s)
  {
    head_ aa_kata_gc_last_run_timestamp_seconds 'Unix time the last kata-gc run finished.'
    printf 'aa_kata_gc_last_run_timestamp_seconds{node="%s"} %s\n' "$NODE" "$now"
    head_ aa_kata_gc_last_exit_code 'Exit code of the last run (0 = ok; 1 = inventory or removal failed).'
    printf 'aa_kata_gc_last_exit_code{node="%s"} %s\n' "$NODE" "$RC"
    head_ aa_kata_gc_last_duration_seconds 'Wall time of the last run.'
    printf 'aa_kata_gc_last_duration_seconds{node="%s"} %s\n' "$NODE" "$(( now - START ))"
    head_ aa_kata_gc_live_shims 'containerd-shim-kata processes seen by the last run.'
    printf 'aa_kata_gc_live_shims{node="%s"} %s\n' "$NODE" "$n_shims"
    head_ aa_kata_gc_known_sandboxes 'Sandbox ids containerd listed in the last run (crictl pods/ps, ctr sandboxes), any runtime.'
    printf 'aa_kata_gc_known_sandboxes{node="%s"} %s\n' "$NODE" "$n_known"
    head_ aa_kata_gc_last_removed 'Stale sandbox state directories removed by the last run.'
    for r in "${ROOTS[@]}"; do
      printf 'aa_kata_gc_last_removed{node="%s",root="%s"} %s\n' "$NODE" "$r" "$(got "$r" removed)"
    done
    head_ aa_kata_gc_removed_total 'Stale sandbox state directories removed since the metric file was created.' counter
    for r in "${ROOTS[@]}"; do
      tot=$(prom_prev "aa_kata_gc_removed_total{node=\"$NODE\",root=\"$r\"}")
      [[ $tot =~ ^[0-9]+$ ]] || tot=0
      printf 'aa_kata_gc_removed_total{node="%s",root="%s"} %s\n' "$NODE" "$r" "$(( tot + $(got "$r" removed) ))"
    done
    head_ aa_kata_gc_last_kept 'State directories left in place by the last run, by why they were kept.'
    for r in "${ROOTS[@]}"; do
      for k in "${KEPT_KINDS[@]}"; do
        printf 'aa_kata_gc_last_kept{node="%s",root="%s",reason="%s"} %s\n' "$NODE" "$r" "$k" "$(got "$r" "$k")"
      done
    done
    head_ aa_kata_gc_last_errors 'Errors in the last run: inventory failed (0/1) or directories that could not be removed.'
    printf 'aa_kata_gc_last_errors{node="%s",kind="inventory"} %s\n' "$NODE" "$inv_err"
    printf 'aa_kata_gc_last_errors{node="%s",kind="remove"} %s\n' "$NODE" "$(( $(got sbs failed) + $(got shared failed) ))"
  } | prom_write || warn "could not write $TEXTFILE_DIR/$PROM_FILE"
  exit "$RC"
}
trap on_exit EXIT

inv_die() { inv_err=1; die "$* (nothing removed)"; }

# A runtime switch can leave old Go roots alongside new state. Only the observed
# 3.32.0 Go layout has cleanup proof; refuse all other layouts before inventory.
CONFIG=${KATA_GC_CONFIG:-/var/lib/rancher/k3s/agent/etc/containerd/config-v3.toml.d/20-kata.toml}
[[ -f $CONFIG && ! -L $CONFIG && -r $CONFIG ]] || inv_die "Kata runtime layout cannot be established"
runtime_paths=$(sed -nE 's/^[[:space:]]*runtime_path[[:space:]]*=[[:space:]]*"([^"]+)"[[:space:]]*$/\1/p' "$CONFIG")
[[ $runtime_paths == /opt/kata/3.32.0/bin/containerd-shim-kata-v2 ]] || inv_die "unsupported Kata runtime layout (only observed 3.32.0 Go cleanup is supported)"


# ---- 1. candidates: what is on disk now (listed before the inventory, see header) --------
declare -A dirs_of=()   # id -> newline-separated "<root key>|<path>" of its directories
scan_root() {  # scan_root KEY ROOT
  local k=$1 root=$2 d id
  [[ ! -L $root ]] || inv_die "state root is a symlink; layout unknown"
  if [[ ! -d $root ]]; then log "$root: absent, nothing to do"; return 0; fi
  for d in "$root"/*; do
    [[ -e $d || -L $d ]] || continue                   # empty glob
    id=${d##*/}
    if [[ ! $id =~ ^[0-9a-f]{64}$ || -L $d || ! -d $d ]]; then tally "$k" other; continue; fi
    dirs_of[$id]=${dirs_of[$id]:+${dirs_of[$id]}$'\n'}"$k|$d"
  done
}
scan_root sbs "$SBS_ROOT"
scan_root shared "$SHARED_ROOT"

# ---- 2. inventory: what is owned ----------------------------------------------------------
declare -A owned=()

# 2a. every pod sandbox containerd lists, in any state (Ready, NotReady, ...)
pods=$("${CRICTL[@]}" pods -q --no-trunc) || inv_die "'${CRICTL[*]} pods' failed"
# 2b. the sandbox of every container, even exited ones (a container record can outlive its
#     pod entry after a crash)
ps_json=$("${CRICTL[@]}" ps -a -o json) || inv_die "'${CRICTL[*]} ps -a' failed"
[[ $ps_json == \{* ]] || inv_die "'${CRICTL[*]} ps -a -o json' did not return a JSON object"
ps_ids=$(grep -oE '"podSandboxId": *"[0-9a-f]{64}"' <<<"$ps_json" | grep -oE '[0-9a-f]{64}' || true)
# 2c. containerd's own sandbox store (containerd 2.x sandbox API; first column is the id)
ctr_out=$("${CTR[@]}" sandboxes ls) || inv_die "'${CTR[*]} sandboxes ls' failed"
ctr_ids=$(awk 'NR>1 && length($1) == 64 && $1 ~ /^[0-9a-f]+$/ {print $1}' <<<"$ctr_out")
while IFS= read -r id; do
  [[ $id =~ ^[0-9a-f]{64}$ ]] && owned[$id]=containerd
done < <(printf '%s\n%s\n%s\n' "$pods" "$ps_ids" "$ctr_ids" | sort -u)
n_known=${#owned[@]}
# Every k3s node runs system pods, so "no sandbox at all" while state directories exist
# means the inventory is wrong (wrong endpoint, containerd state lost), not that all are stale.
(( ${#dirs_of[@]} == 0 || n_known > 0 )) \
  || inv_die "containerd lists no sandbox at all but ${#dirs_of[@]} sandbox id(s) have state on disk; not trusting an empty inventory"

# 3. running kata processes, by argv[0]: the shim, and the VMM / virtiofsd it starts (they
#    carry the sandbox id in --shared-dir / --api-socket paths). Any 64-hex in their
#    arguments is treated as owned. Processes that exit mid-scan are skipped.
for c in "$PROC"/[0-9]*/cmdline; do
  [[ -e $c ]] || continue                              # empty glob
  argv=()
  mapfile -d '' -t argv 2>/dev/null <"$c" || inv_die "process ownership cannot be read"
  (( ${#argv[@]} )) || continue                        # kernel threads, zombies
  case ${argv[0]##*/} in
    containerd-shim-kata*)
      [[ ${argv[0]} != */runtime-rs/* && ${argv[0]} != */*-rs/* ]] || inv_die "runtime-rs shim present; cleanup layout unsupported"
      n_shims=$(( n_shims + 1 )) ;;
    cloud-hypervisor|virtiofsd|qemu-system-*|firecracker|dragonball|kata-runtime) ;;
    *) continue ;;
  esac
  for a in "${argv[@]}"; do
    while [[ $a =~ ([0-9a-f]{64})(.*) ]]; do
      owned[${BASH_REMATCH[1]}]=process
      a=${BASH_REMATCH[2]}
    done
  done
done

# 4. the sandbox's own files. Sockets: the listener is alive iff the path is in the kernel's
#    unix socket table (a stale socket file is not). Read once.
if [[ -r $PROC/net/unix ]]; then unix_list=$(<"$PROC/net/unix"); unix_ok=1; else unix_list=''; unix_ok=0; fi
state_owned() {  # state_owned ID -> 0 when a socket or pid file shows an owner, or cannot be judged
  local id=$1 root d files f pid
  for root in "$SBS_ROOT" "$SHARED_ROOT"; do
    d=$root/$id
    [[ -d $d && ! -L $d ]] || continue
    # -xdev: never walk into a mount. find failing (dir vanished, no permission) is "unclear".
    files=$(find "$d" -xdev \( -type s -o -name '*.sock' \) 2>/dev/null) || return 0
    while IFS= read -r f; do
      [[ -n $f ]] || continue
      (( unix_ok )) || return 0
      if grep -qF -- "$f" <<<"$unix_list"; then return 0; fi
    done <<<"$files"
    files=$(find "$d" -xdev -type f \( -name pid -o -name '*.pid' \) 2>/dev/null) || return 0
    while IFS= read -r f; do
      [[ -n $f ]] || continue
      pid=''
      IFS= read -r pid 2>/dev/null <"$f" || true       # last line without newline still sets pid
      pid=${pid//[[:space:]]/}
      [[ $pid =~ ^[0-9]+$ ]] || return 0               # unreadable or not a pid: keep
      if [[ -d $PROC/$pid ]]; then return 0; fi
    done <<<"$files"
  done
  return 1
}
declare -A state_cache=()
state_owned_cached() {  # decided once per id, before any removal changes what state_owned sees
  local id=$1
  if [[ -z ${state_cache[$id]:-} ]]; then
    if state_owned "$id"; then state_cache[$id]=1; else state_cache[$id]=0; fi
  fi
  [[ ${state_cache[$id]} == 1 ]]
}

# 5. host mount table, once; checked per directory below (field 5 of mountinfo = mount point)
mounts=$(awk '{print $5}' "$PROC/self/mountinfo") || inv_die "cannot read $PROC/self/mountinfo"
is_mounted() {  # a mount at, or anywhere below, $1
  awk -v p="$1" '$0 == p || index($0, p "/") == 1 {f = 1} END {exit !f}' <<<"$mounts"
}

log "node $NODE: ${n_known} sandboxes known to containerd, ${n_shims} kata shim(s) running, ${#dirs_of[@]} sandbox id(s) with state on disk, min age ${MIN_AGE}s$( (( DRY_RUN )) && printf ' (dry run)' || true)"

# ---- sweep ------------------------------------------------------------------------------
NOW=$(date +%s)
sweep_id() {  # one verdict for the whole pair (sbs + shared), applied to every directory of the id
  local id=$1 item k d mtime verdict=remove
  local -a items=()
  mapfile -t items <<<"${dirs_of[$id]}"
  if [[ -n ${owned[$id]:-} ]]; then
    verdict=live
  elif state_owned_cached "$id"; then
    verdict=state
  else
    for item in "${items[@]}"; do
      d=${item#*|}
      mtime=$(stat -c %Y -- "$d" 2>/dev/null) || continue   # vanished under us: fine
      if (( NOW - mtime < MIN_AGE )); then verdict=young; break; fi
      if is_mounted "$d"; then
        verdict=mounted
        warn "$d: unowned and old but something is mounted at or below it; kept"
        break
      fi
    done
  fi
  for item in "${items[@]}"; do
    k=${item%%|*} d=${item#*|}
    if [[ $verdict != remove ]]; then tally "$k" "$verdict"; continue; fi
    [[ -e $d ]] || continue                              # already gone: nothing to count
    if (( DRY_RUN )); then
      mtime=$(stat -c %Y -- "$d" 2>/dev/null) || mtime=$NOW
      log "would remove $d ($(( NOW - mtime ))s old)"; tally "$k" removed; continue
    fi
    # --one-file-system: never descend into a mount that appeared after the mount-table read.
    if rm -rf --one-file-system -- "$d"; then
      tally "$k" removed
    else
      tally "$k" failed; warn "rm -rf $d failed"
    fi
  done
}
for id in $(printf '%s\n' "${!dirs_of[@]}" | sort); do sweep_id "$id"; done

if (( DRY_RUN )); then verb='would remove'; else verb=removed; fi
for r in "${ROOTS[@]}"; do
  log "$r: live $(got "$r" live), $verb $(got "$r" removed), kept by state $(got "$r" state), too young $(got "$r" young), mounted-kept $(got "$r" mounted), failed $(got "$r" failed), other $(got "$r" other)"
done
fails=$(( $(got sbs failed) + $(got shared failed) ))
(( fails == 0 )) || { warn "$fails removal(s) failed"; exit 1; }
RC=0

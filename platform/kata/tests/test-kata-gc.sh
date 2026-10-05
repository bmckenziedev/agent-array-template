#!/usr/bin/env bash
# test-kata-gc.sh -- run kata-gc.sh against a fake world and check what it keeps and removes.
#
#   bash platform/kata/tests/test-kata-gc.sh        (Git Bash on the workstation, or any Linux bash)
#
# Nothing here touches a real node: a temp dir stands in for /run/vc/sbs, the kata shared
# dir, the textfile-collector dir and /proc (KATA_GC_PROC: fake process table, mount table
# and unix socket table). crictl, ctr, id and rm are stubs found through PATH; flock is
# stubbed only where the real one is missing (Git Bash).
#
# What it proves: orphans are removed; a sandbox owned by crictl, by a crictl container,
# by ctr, by a kata shim, by a VMM, by a live socket or by a live pid file is kept; one whose
# ownership cannot be judged is kept; a young or mounted sandbox is kept (both halves of the
# pair); decoy processes do not count as owners; non-sandbox entries and symlinks are never
# touched; --dry-run changes nothing; a missing or empty /run is fine; every inventory
# failure, and an empty inventory, removes nothing and exits non-zero; a failed removal is
# counted and exits non-zero; the metric file is valid, atomic and cumulative; no secret from
# persist.json reaches the output or the metrics.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
gc=$here/../kata-gc.sh
[[ -f $gc ]] || { printf 'kata-gc.sh not found next to tests/\n' >&2; exit 2; }

REAL_RM=$(command -v rm)
REAL_FLOCK=$(command -v flock || true)
T=$(mktemp -d)
cleanup() { "$REAL_RM" -rf -- "$T"; }
trap cleanup EXIT

BIN=$T/bin FAKE=$T/fake PROC=$T/proc OUT=$T/metrics
SBS=$T/run/vc/sbs SHARED=$T/run/kata/shared/sandboxes
PROM=$OUT/aa_kata_gc.prom
SECRET=SECRET-MARKER-9f3a7c
mkdir -p "$BIN"

# ---- stubs ---------------------------------------------------------------------------------
cat >"$BIN/crictl" <<'EOF'
#!/usr/bin/env bash
[[ ! -e $FAKE/crictl.fail ]] || { echo "crictl: connection refused" >&2; exit 1; }
case "${1:-} ${2:-}" in
  "pods -q") cat "$FAKE/pods" ;;
  "ps -a")   cat "$FAKE/ps.json" ;;
  *) echo "crictl stub: unexpected arguments: $*" >&2; exit 2 ;;
esac
EOF
cat >"$BIN/ctr" <<'EOF'
#!/usr/bin/env bash
[[ ! -e $FAKE/ctr.fail ]] || { echo "ctr: connection refused" >&2; exit 1; }
case "${1:-} ${2:-}" in
  "sandboxes ls") cat "$FAKE/ctr.out" ;;
  *) echo "ctr stub: unexpected arguments: $*" >&2; exit 2 ;;
esac
EOF
cat >"$BIN/id" <<'EOF'
#!/usr/bin/env bash
if [[ ${1:-} == -u ]]; then printf '%s\n' "${FAKE_UID:-0}"; else exit 1; fi
EOF
# rm that fails for one chosen sandbox id (to prove failed removals are counted), else real rm.
cat >"$BIN/rm" <<'EOF'
#!/usr/bin/env bash
for a in "$@"; do
  if [[ $a != -* && $a != "$KATA_GC_SBS_ROOT"/* && $a != "$KATA_GC_SHARED_ROOT"/* && $a != "$TEXTFILE_DIR"/* && $a != "$KATA_GC_PROC"/* ]]; then
    echo "rm stub: refusing path outside fixture roots" >&2; exit 2
  fi
  if [[ -n ${FAKE_RM_FAIL_ID:-} && $a == *"$FAKE_RM_FAIL_ID"* ]]; then
    echo "rm: cannot remove '$a': Permission denied" >&2; exit 1
  fi
done
exec "$REAL_RM" "$@"
EOF
if [[ -z $REAL_FLOCK ]]; then
  cat >"$BIN/flock" <<'EOF'
#!/usr/bin/env bash
[[ -z ${FAKE_LOCK_HELD:-} ]]
EOF
fi
chmod +x "$BIN"/*

export PATH="$BIN:$PATH" FAKE REAL_RM
export KATA_GC_SBS_ROOT=$SBS KATA_GC_SHARED_ROOT=$SHARED KATA_GC_PROC=$PROC
export KATA_GC_CONFIG=$T/20-kata.toml
export KATA_GC_LOCK=$T/lock KATA_GC_NODE=testnode CRICTL=crictl CTR=ctr TEXTFILE_DIR=$OUT
unset KATA_GC_MIN_AGE_SEC FAKE_UID FAKE_RM_FAIL_ID FAKE_LOCK_HELD

# ---- tiny test framework --------------------------------------------------------------------
pass=0 failed=0
ok()  { pass=$((pass + 1)); printf 'ok   %s\n' "$1"; }
bad() { failed=$((failed + 1)); printf 'FAIL %s\n' "$1" >&2; }
check() { local desc=$1; shift; if "$@"; then ok "$desc"; else bad "$desc"; fi; }
eq() { if [[ $2 == "$3" ]]; then ok "$1"; else bad "$1 (want '$2', got '$3')"; fi; }
present() { [[ -e $1 || -L $1 ]]; }
absent() { ! present "$1"; }
has() { [[ $OUTPUT == *"$1"* ]]; }
lacks() { [[ $OUTPUT != *"$1"* ]]; }
nonzero() { (( RC != 0 )); }
prom() { awk -v m="$1" '$1 == m {print $2}' "$PROM"; }
mval() { prom "aa_kata_gc_$1{node=\"testnode\"${2:-}}"; }   # mval metric ',label="x"'

# ---- fake world builders ----------------------------------------------------------------------
sid() { printf '%064x' $((0xfeed00 + $1)); }
reset() {
  printf 'runtime_path = "/opt/kata/3.32.0/bin/containerd-shim-kata-v2"\n' >"$KATA_GC_CONFIG"
  "$REAL_RM" -rf -- "$SBS" "$SHARED" "$PROC" "$FAKE" "$OUT" "$T/outside"
  mkdir -p "$SBS" "$SHARED" "$PROC/self" "$PROC/net" "$FAKE" "$T/outside"
  : >"$FAKE/pods"
  printf '{"containers": []}\n' >"$FAKE/ps.json"
  printf 'ID                CREATED   RUNTIME\n' >"$FAKE/ctr.out"
  printf '1 0 0:1 / / rw - rootfs rootfs rw\n' >"$PROC/self/mountinfo"
  printf 'Num RefCount Protocol Flags Type St Inode Path\n' >"$PROC/net/unix"
  printf 'keep me\n' >"$T/outside/sentinel"
}
mk() {  # mk ID [AGE_SECONDS=7200] [both|sbs|shared]: one sandbox's state, last touched AGE ago
  local id=$1 age=${2:-7200} which=${3:-both} t
  t=$(( $(date +%s) - age ))
  if [[ $which != shared ]]; then
    mkdir -p "$SBS/$id/c1"
    printf '{"env":{"TOKEN":"%s"}}\n' "$SECRET" >"$SBS/$id/persist.json"
    : >"$SBS/$id/shim-monitor.sock"          # a stale socket: nothing listens on it
    touch -d "@$t" "$SBS/$id/c1" "$SBS/$id"
  fi
  if [[ $which != sbs ]]; then
    mkdir -p "$SHARED/$id/mounts" "$SHARED/$id/private"
    touch -d "@$t" "$SHARED/$id/mounts" "$SHARED/$id/private" "$SHARED/$id"
  fi
}
age() {  # age ID SECONDS: re-stamp one sandbox's directories (adding a file bumps the dir mtime)
  local id=$1 t d
  t=$(( $(date +%s) - $2 ))
  for d in "$SBS/$id" "$SHARED/$id"; do if [[ -d $d ]]; then touch -d "@$t" "$d"; fi; done
}
fake_proc() {  # fake_proc PID ARGV...: a process in the fake /proc (cmdline is NUL-separated)
  local pid=$1; shift
  mkdir -p "$PROC/$pid"
  printf '%s\0' "$@" >"$PROC/$pid/cmdline"
}
listed_pods() { printf '%s\n' "$@" >"$FAKE/pods"; }
run() {  # run [ARGS]: OUTPUT = stdout+stderr, RC = exit status; never aborts the test
  set +e
  if (( $# == 0 )); then set -- --yes; fi
  OUTPUT=$(bash "$gc" "$@" 2>&1)
  RC=$?
  set -e
}
valid_prom() {  # the metric file is well-formed exposition text, complete and without leftovers
  local f=$PROM
  [[ -s $f ]] || return 1
  ! grep -vEq '^(# HELP [a-z_]+ .+|# TYPE [a-z_]+ (gauge|counter)|[a-z_]+(\{[a-z_]+="[^"]*"(,[a-z_]+="[^"]*")*\})? [0-9]+)$' "$f" || return 1
  awk '/^# TYPE/ {t[$3] = 1; next} /^#/ {next} {n = $1; sub(/\{.*/, "", n); if (!(n in t)) bad = 1} END {exit bad}' "$f" || return 1
  [[ -z $(grep -v '^#' "$f" | awk '{print $1}' | sort | uniq -d) ]] || return 1
  [[ -z $(ls -A "$OUT" | grep -vx 'aa_kata_gc.prom' || true) ]]
}
tree_of() { find "$SBS" "$SHARED" -mindepth 1 | sort | sed "s|$T||"; }

# =================================================================================================
echo "== static checks"
for f in kata-gc.sh install-kata-gc.sh tests/test-kata-gc.sh; do
  check "bash -n $f" bash -n "$here/../$f"
done
for f in kata-gc.sh install-kata-gc.sh kata-gc.service kata-gc.timer tests/test-kata-gc.sh; do
  check "$f has LF line endings" bash -c '! grep -q $'"'"'\r'"'"' "$1"' _ "$here/../$f"
done
check "service runs the installed script" grep -qx 'ExecStart=/usr/local/sbin/kata-gc.sh --yes' "$here/../kata-gc.service"
check "timer is hourly with a randomized delay" bash -c \
  'grep -qx "OnUnitActiveSec=1h" "$1" && grep -Eq "^RandomizedDelaySec=[0-9]+" "$1"' _ "$here/../kata-gc.timer"
check "script never uses pgrep, pkill or ps (process table comes from /proc)" bash -c \
  '! grep -Eq "^[^#]*(\b(pgrep|pkill)\b|(^|[;&|(])[[:space:]]*ps[[:space:]])" "$1"' _ "$gc" --yes

# =================================================================================================
echo "== one world with every kind of sandbox"
reset
orphan=$(sid 1);  orphan_sbs=$(sid 2); orphan_shared=$(sid 3)
cri=$(sid 4);     cri_ps=$(sid 5);     ctr_only=$(sid 6)
shim=$(sid 7);    vmm=$(sid 8)
live_sock=$(sid 9); live_pid=$(sid 10); dead_pid=$(sid 11); garbage_pid=$(sid 12)
young=$(sid 13);  mounted=$(sid 14);   decoy_bash=$(sid 15); decoy_runc=$(sid 16)
pair_young=$(sid 17)

mk "$orphan"; mk "$orphan_sbs" 7200 sbs; mk "$orphan_shared" 7200 shared
for id in "$cri" "$cri_ps" "$ctr_only" "$shim" "$vmm" "$live_sock" "$live_pid" "$dead_pid" \
          "$garbage_pid" "$young" "$mounted" "$decoy_bash" "$decoy_runc" "$pair_young"; do mk "$id"; done
mk "$young" 30
mk "$pair_young"; touch -d "@$(( $(date +%s) - 10 ))" "$SHARED/$pair_young"   # sbs old, shared young

listed_pods "$cri"
printf '{"containers": [{"id": "aa", "podSandboxId":  "%s"}]}\n' "$cri_ps" >"$FAKE/ps.json"
printf 'ID                CREATED   RUNTIME\n%s  example  io.containerd.kata.v2\n' "$ctr_only" >"$FAKE/ctr.out"
fake_proc 100 /opt/kata/3.32.0/bin/containerd-shim-kata-v2 -namespace k8s.io -address /run/k3s/containerd/containerd.sock -publish-binary '' -id "$shim"
fake_proc 101 /opt/kata/libexec/virtiofsd "--shared-dir=$SHARED/$vmm/shared" --socket-path=/x
fake_proc 102 bash -c "echo $decoy_bash"                      # mentions an id, is not a kata process
fake_proc 103 /usr/bin/containerd-shim-runc-v2 -namespace k8s.io -id "$decoy_runc"
fake_proc 4321 sleep 100
printf '%s %s\n' "99 1 0:99 / $SHARED/$mounted/shared rw - tmpfs tmpfs rw" >>"$PROC/self/mountinfo"
printf '0000000000000000: 00000002 00000000 00010000 0001 01 777 %s/shim-monitor.sock\n' "$SBS/$live_sock" >>"$PROC/net/unix"
printf '4321\n' >"$SBS/$live_pid/pid"
printf '99999\n' >"$SBS/$dead_pid/pid"
printf 'not-a-pid\n' >"$SBS/$garbage_pid/pid"
# Entries that are not sandbox directories, and a symlink out of a sandbox directory.
mkdir -p "$SBS/not-an-id"
: >"$SBS/$(sid 18)"                                           # a file with a valid id name
mkdir -p "$SBS/$(sid 19 | tr a-f A-F)"                        # right length, upper-case hex
other=3
symlinks=0
# Git Bash's ln -s silently makes a copy unless native symlinks are enabled: keep only real links.
mklink() { if ln -s "$1" "$2" 2>/dev/null && [[ -L $2 ]]; then return 0; fi; "$REAL_RM" -rf -- "$2"; return 1; }
if mklink "$T/outside" "$SBS/$(sid 20)"; then other=4; fi
if mklink "$T/outside" "$SBS/$orphan/link"; then symlinks=1; fi
for id in "$orphan" "$dead_pid" "$live_pid" "$garbage_pid"; do age "$id" 7200; done   # files were added above
before_tree=$(tree_of)

run --dry-run
eq "dry run exits 0" 0 "$RC"
has "(dry run)" && ok "dry run says so" || bad "dry run says so"
has "3 sandboxes known to containerd, 1 kata shim(s) running" && ok "inventory counts: 3 containerd, 1 shim" || bad "inventory counts ($OUTPUT)"
has "sbs: live 5, would remove 5, kept by state 3, too young 2, mounted-kept 1, failed 0, other $other" \
  && ok "dry-run sbs counts" || bad "dry-run sbs counts ($OUTPUT)"
has "shared: live 5, would remove 5, kept by state 3, too young 2, mounted-kept 1, failed 0, other 0" \
  && ok "dry-run shared counts" || bad "dry-run shared counts"
eq "dry run lists every dir it would remove" 10 "$(grep -c 'would remove /' <<<"$OUTPUT")"
eq "dry run changed nothing on disk" "$before_tree" "$(tree_of)"
check "dry run wrote no metrics" absent "$PROM"
check "dry run output has no secret" lacks "$SECRET"

run
eq "real run exits 0" 0 "$RC"
has "sbs: live 5, removed 5, kept by state 3, too young 2, mounted-kept 1, failed 0, other $other" \
  && ok "real-run sbs counts" || bad "real-run sbs counts ($OUTPUT)"
has "shared: live 5, removed 5, kept by state 3, too young 2, mounted-kept 1, failed 0, other 0" \
  && ok "real-run shared counts" || bad "real-run shared counts"
check "run output has no secret" lacks "$SECRET"
check "no 'would remove' lines outside dry run" lacks "would remove"
for id in "$orphan" "$orphan_sbs" "$orphan_shared" "$dead_pid" "$decoy_bash" "$decoy_runc"; do
  check "orphan ${id:58} removed from both roots" bash -c '[[ ! -e $1/$3 && ! -e $2/$3 ]]' _ "$SBS" "$SHARED" "$id"
done
check "orphan with a stale socket and a dead pid file is gone" absent "$SBS/$dead_pid"
for pair in "crictl pod:$cri" "crictl container:$cri_ps" "ctr sandbox:$ctr_only" "kata shim:$shim" \
            "VMM/virtiofsd:$vmm" "live socket:$live_sock" "live pid file:$live_pid" \
            "unreadable pid file:$garbage_pid" "young:$young" "mounted:$mounted" "half-young pair:$pair_young"; do
  desc=${pair%%:*} id=${pair#*:}
  check "kept ($desc): sbs and shared both intact" bash -c '[[ -d $1/$3 && -d $2/$3 ]]' _ "$SBS" "$SHARED" "$id"
done
check "kept: persist.json of a live sandbox untouched" test -f "$SBS/$cri/persist.json"
check "non-id directory untouched" test -d "$SBS/not-an-id"
check "file with a valid id name untouched" test -f "$SBS/$(sid 18)"
check "upper-case id directory untouched" test -d "$SBS/$(sid 19 | tr a-f A-F)"
check "outside data untouched" test -f "$T/outside/sentinel"
if (( symlinks )); then check "symlink inside an orphan was removed, not followed" absent "$SBS/$orphan"; fi
(( other == 3 )) || check "symlinked 'sandbox' directory untouched" test -L "$SBS/$(sid 20)"

echo "== metrics of that run"
check "metric file is valid, complete, atomic (no leftovers)" valid_prom
eq "last_removed sbs" 5 "$(mval last_removed ',root="sbs"')"
eq "last_removed shared" 5 "$(mval last_removed ',root="shared"')"
eq "kept live sbs" 5 "$(mval last_kept ',root="sbs",reason="live"')"
eq "kept state sbs" 3 "$(mval last_kept ',root="sbs",reason="state"')"
eq "kept young shared" 2 "$(mval last_kept ',root="shared",reason="young"')"
eq "kept mounted shared" 1 "$(mval last_kept ',root="shared",reason="mounted"')"
eq "kept other sbs" "$other" "$(mval last_kept ',root="sbs",reason="other"')"
eq "errors inventory" 0 "$(mval last_errors ',kind="inventory"')"
eq "errors remove" 0 "$(mval last_errors ',kind="remove"')"
eq "exit code metric" 0 "$(mval last_exit_code)"
eq "live shims metric" 1 "$(mval live_shims)"
eq "known sandboxes metric" 3 "$(mval known_sandboxes)"
ts=$(mval last_run_timestamp_seconds)
check "last-run timestamp is now" bash -c '(( $(date +%s) - $1 < 60 && $1 > 0 ))' _ "$ts"
eq "removed_total sbs" 5 "$(mval removed_total ',root="sbs"')"
check "metric file is world-readable" bash -c '[[ $(stat -c %a "$1") == 644 ]]' _ "$PROM"
check "metrics hold no secret" bash -c '! grep -q "$1" "$2"' _ "$SECRET" "$PROM"

echo "== second run: idempotent, counters cumulative"
run
eq "second run exits 0" 0 "$RC"
has "sbs: live 5, removed 0, kept by state 3, too young 2" && ok "nothing left to remove" || bad "second-run counts ($OUTPUT)"
eq "last_removed resets" 0 "$(mval last_removed ',root="sbs"')"
eq "removed_total survives" 5 "$(mval removed_total ',root="sbs"')"
new_orphan=$(sid 30); mk "$new_orphan"
run
eq "removed_total accumulates" 6 "$(mval removed_total ',root="sbs"')"
eq "...for shared too" 6 "$(mval removed_total ',root="shared"')"

echo "== dry run leaves existing metrics alone"
hash_before=$(cat "$PROM")
mk "$(sid 31)"
run --dry-run
eq "dry run: metric file unchanged" "$hash_before" "$(cat "$PROM")"
check "dry run: orphan still there" test -d "$SBS/$(sid 31)"

echo "== ownership that cannot be judged"
reset
a=$(sid 40); mk "$a"; listed_pods "$(sid 41)"             # some other sandbox keeps containerd non-empty
rm -f "$PROC/net/unix"
run
eq "unix socket table unreadable: exit 0" 0 "$RC"
check "unix socket table unreadable: sandbox with a socket is kept" test -d "$SBS/$a"
eq "...counted as kept by state" 1 "$(mval last_kept ',root="sbs",reason="state"')"
printf 'Num RefCount Protocol Flags Type St Inode Path\n' >"$PROC/net/unix"
run
check "table readable and nothing listens: now removed" absent "$SBS/$a"

echo "== inventory failures remove nothing"
for scenario in crictl_fails ctr_fails crictl_garbage empty_inventory mountinfo_missing; do
  reset
  victim=$(sid 50); mk "$victim"
  listed_pods "$(sid 51)"
  case $scenario in
    crictl_fails)     : >"$FAKE/crictl.fail" ;;
    ctr_fails)        : >"$FAKE/ctr.fail" ;;
    crictl_garbage)   printf 'error: not json\n' >"$FAKE/ps.json" ;;
    empty_inventory)  : >"$FAKE/pods" ;;
    mountinfo_missing) "$REAL_RM" -f "$PROC/self/mountinfo" ;;
  esac
  run
  check "$scenario: exits non-zero" nonzero
  check "$scenario: orphan-looking dirs kept" bash -c '[[ -d $1/$3 && -d $2/$3 ]]' _ "$SBS" "$SHARED" "$victim"
  eq "$scenario: exit-code metric" 1 "$(mval last_exit_code)"
  eq "$scenario: inventory error metric" 1 "$(mval last_errors ',kind="inventory"')"
  eq "$scenario: nothing counted as removed" 0 "$(mval last_removed ',root="sbs"')"
  check "$scenario: metric file valid" valid_prom
done
reset; mk "$(sid 52)"
run
has "not trusting an empty inventory" && ok "empty inventory is named in the error" || bad "empty inventory message ($OUTPUT)"

echo "== unsupported rs and unknown layouts refuse live and dead fixtures"
for layout in rs unknown missing mixed; do
  reset
  live=$(sid 80); dead=$(sid 81); unclear=$(sid 82)
  mk "$live"; mk "$dead"; mk "$unclear"; listed_pods "$live"
  printf 'unknown\n' >"$SBS/$unclear/pid"; age "$unclear" 7200
  case $layout in
    rs) printf 'runtime_path = "/opt/kata/4.2.0-rs/runtime-rs/bin/containerd-shim-kata-v2"\n' >"$KATA_GC_CONFIG" ;;
    unknown) printf 'runtime_path = "/unverified/shim"\n' >"$KATA_GC_CONFIG" ;;
    missing) "$REAL_RM" -f "$KATA_GC_CONFIG" ;;
    mixed) fake_proc 808 /opt/kata/4.2.0-rs/runtime-rs/bin/containerd-shim-kata-v2 -id "$live" ;;
  esac
  before_tree=$(tree_of)
  for mode in apply dry; do
    if [[ $mode == dry ]]; then run --dry-run; else run; fi
    check "$layout $mode: refused" nonzero
    eq "$layout $mode: live/dead/unknown all untouched" "$before_tree" "$(tree_of)"
  done
done

echo "== a failed removal is counted and fails the run"
reset
good=$(sid 60); bad_id=$(sid 61)
mk "$good"; mk "$bad_id"; listed_pods "$(sid 62)"
FAKE_RM_FAIL_ID=$bad_id run
check "failed removal: exits non-zero" nonzero
check "failed removal: the other orphan was still removed" absent "$SBS/$good"
check "failed removal: the stuck dir is still there" test -d "$SBS/$bad_id"
eq "failed removal: errors{remove} = 2 (sbs + shared)" 2 "$(mval last_errors ',kind="remove"')"
eq "failed removal: removed counts only the good one" 1 "$(mval last_removed ',root="sbs"')"
eq "failed removal: exit code metric" 1 "$(mval last_exit_code)"
eq "failed removal: not an inventory error" 0 "$(mval last_errors ',kind="inventory"')"

echo "== empty and missing /run state"
reset
run
eq "empty roots: exit 0" 0 "$RC"
eq "empty roots: metrics are zero" 0 "$(mval last_removed ',root="sbs"')"
eq "empty roots: exit code metric" 0 "$(mval last_exit_code)"
check "empty roots: metric file valid" valid_prom
reset
"$REAL_RM" -rf -- "$SBS" "$SHARED"
run
eq "missing roots: exit 0" 0 "$RC"
has "absent, nothing to do" && ok "missing roots: says nothing to do" || bad "missing roots message ($OUTPUT)"
eq "missing roots: metrics are zero" 0 "$(mval last_kept ',root="shared",reason="live"')"
check "missing roots: metric file valid" valid_prom
run --dry-run
eq "missing roots, dry run: exit 0" 0 "$RC"

echo "== arguments, environment, lock"
reset; mk "$(sid 70)"; listed_pods "$(sid 71)"; run; hash_before=$(cat "$PROM")
mk "$(sid 72)"
run --bogus;            eq "unknown argument: exit 64" 64 "$RC"
run --dry-run extra;    eq "extra argument: exit 64" 64 "$RC"
run --help;             eq "--help: exit 0" 0 "$RC"
KATA_GC_MIN_AGE_SEC=abc run; check "non-numeric min age is refused" nonzero
FAKE_UID=1000 run;      check "non-root is refused" nonzero
has "run as root" && ok "...with a clear message" || bad "run-as-root message"
eq "refused runs left the metrics alone" "$hash_before" "$(cat "$PROM")"
check "refused runs removed nothing" test -d "$SBS/$(sid 72)"
if [[ -n $REAL_FLOCK ]]; then
  # The holder must outlive the child: without "exit $?" bash would exec the child in its place,
  # and the child closing its inherited fd 9 would release the lock.
  set +e; OUTPUT=$( (exec 9>"$T/lock"; flock -n 9; bash "$gc" --yes; exit $?) 2>&1 ); RC=$?; set -e
else
  FAKE_LOCK_HELD=1 run
fi
check "second instance while the lock is held: refused" nonzero
has "another kata-gc run holds" && ok "...with a clear message" || bad "lock message ($OUTPUT)"
eq "refused instance did not overwrite the metrics" "$hash_before" "$(cat "$PROM")"
check "refused instance removed nothing" test -d "$SBS/$(sid 72)"
KATA_GC_MIN_AGE_SEC=0 run
eq "min age 0 is accepted" 0 "$RC"
check "...and then removes the just-made orphan" absent "$SBS/$(sid 72)"

echo
if (( failed )); then
  printf 'FAIL: %d of %d checks failed\n' "$failed" "$((pass + failed))" >&2
  exit 1
fi
printf 'PASS: %d checks (orphan removal; kept: crictl/ctr/shim/VMM/socket/pid/young/mounted/unclear; dry-run; missing and empty /run; inventory and removal failures; metrics; lock)\n' "$pass"

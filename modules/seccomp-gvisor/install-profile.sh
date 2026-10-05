#!/usr/bin/env bash
# install-profile.sh -- check or explicitly install the Localhost seccomp profile on a node
# (README.md). Run as root ON THE NODE from a copy of modules/seccomp-gvisor/. Idempotent.
#
#   scp -r modules/seccomp-gvisor <node>:/tmp/agent-array-seccomp \
#     && ssh <node> 'bash /tmp/agent-array-seccomp/install-profile.sh --yes; rm -rf /tmp/agent-array-seccomp'
#   bash install-profile.sh --check       exit 0 only if the node has exactly this copy's profile
#   bash install-profile.sh --uninstall --yes   remove it (new pods that name it then fail to start;
#                                         running pods keep the filter they started with)
#
# Installs <kubelet root>/seccomp/profiles/seccomp-gvisor/runtime-default-clone3-enosys.json
# (0644 root; kubelet root /var/lib/kubelet on k3s), which pods name as
#   seccompProfile: { type: Localhost, localhostProfile: profiles/seccomp-gvisor/runtime-default-clone3-enosys.json }
# containerd reads the file when a container is created, so nothing is restarted.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
NAME=runtime-default-clone3-enosys.json
SRC=$here/profiles/$NAME
KUBELET_ROOT=${KUBELET_ROOT:-/var/lib/kubelet}
DEST_DIR=$KUBELET_ROOT/seccomp/profiles/seccomp-gvisor
DEST=$DEST_DIR/$NAME

die() { printf '[install-profile] ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[install-profile] %s\n' "$*"; }

MODE=
YES=0
for arg in "$@"; do
  case "$arg" in
    --yes) YES=1 ;;
    --check) MODE=check ;;
    --uninstall) MODE=uninstall ;;
    *) die "usage: $0 [--check|--uninstall] [--yes]" ;;
  esac
done
if [[ -z $MODE ]]; then
  if (( YES )); then MODE=install; else MODE=check; fi
fi
if [ "$MODE" = uninstall ] && (( ! YES )); then
  log "Read-only plan: remove installed profile; --yes required"
  exit 0
fi

[ "$(id -u)" -eq 0 ] || die "run as root"
# A kubelet with another --root-dir would look for the profile elsewhere and every pod
# naming it would fail to start; refuse rather than install where nobody reads it.
[ -d "$KUBELET_ROOT/pods" ] || die "$KUBELET_ROOT/pods not found: is this a k3s node (set KUBELET_ROOT)?"
if grep -rqs -- 'root-dir' /etc/rancher/k3s/config.yaml /etc/rancher/k3s/config.yaml.d/ 2>/dev/null; then
  die "k3s config sets a kubelet root-dir; set KUBELET_ROOT to it"
fi

# Refuse symlinks at every target component, including dangling ones.
for component in "$KUBELET_ROOT" "$KUBELET_ROOT/seccomp" "$KUBELET_ROOT/seccomp/profiles" "$DEST_DIR" "$DEST"; do
  [ ! -L "$component" ] || die "symlink target refused: $component"
done
[ ! -e "$DEST" ] || [ -f "$DEST" ] || die "target is not a regular file"

if [ "$MODE" = uninstall ]; then
  rm -f "$DEST"
  rmdir "$DEST_DIR" 2>/dev/null || true      # only if empty: other profiles stay
  log "removed $DEST"
  exit 0
fi

[ -s "$SRC" ] || die "$SRC missing (run from a copy of modules/seccomp-gvisor/)"
# Validate against the reference and exact canonical build before any write.
python3 "$here/seccomp_profile.py" check "$SRC" || die "profile failed strictness check"
python3 - "$here" "$SRC" <<'PY'
import pathlib, sys
sys.path.insert(0, sys.argv[1])
import seccomp_profile as p
if pathlib.Path(sys.argv[2]).read_bytes() != p.dumps(p.build(p.load(p.REFERENCE))).encode():
    raise SystemExit("profile differs from canonical build")
PY
want=$(sha256sum "$SRC" | cut -d' ' -f1)
if [ "$MODE" = check ]; then
  [ -f "$DEST" ] || die "$DEST is not installed"
  have=$(sha256sum "$DEST" | cut -d' ' -f1)
  [ "$have" = "$want" ] || die "$DEST differs from this copy (have $have, want $want)"
  [ "$(stat -c '%u:%g:%a' "$DEST")" = "0:0:644" ] || die "profile metadata must be root:root 0644"
  log "OK $DEST sha256 $have"
  exit 0
fi

# Refuse a file containerd could not parse: a broken profile makes pods fail to START,
# it never makes them run unfiltered, but catch it here rather than at the next Job.
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d["defaultAction"] == "SCMP_ACT_ERRNO" and d["syscalls"]' "$SRC" \
  || die "$SRC is not a valid profile"

install -d -m 0755 -o root -g root "$KUBELET_ROOT/seccomp" "$KUBELET_ROOT/seccomp/profiles" "$DEST_DIR"
if [ -f "$DEST" ] && [ "$(sha256sum "$DEST" | cut -d' ' -f1)" = "$want" ]; then
  chown root:root "$DEST"
  chmod 0644 "$DEST"
  log "already current: $DEST sha256 $want"
else
  # write beside the target, then rename: a pod starting meanwhile sees the old or the new
  # file, never half of one
  tmp=$(mktemp "$DEST_DIR/.${NAME}.XXXXXX")
  trap 'rm -f "$tmp"' EXIT
  install -m 0644 -o root -g root "$SRC" "$tmp"
  [ "$(sha256sum "$tmp" | cut -d' ' -f1)" = "$want" ] || die "staged hash differs"
  mv -fT "$tmp" "$DEST"
  trap - EXIT
  log "installed $DEST sha256 $want"
fi
# Measured with this runsc; a different release must be re-verified (tests/live/verify-threads.sh).
log "runsc on this node: $(runsc --version 2>/dev/null | head -1 || echo 'not installed')"

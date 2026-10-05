#!/usr/bin/env bash
# Install Kata Containers (VM-isolated pods) on a k3s node as the containerd runtime
# handler "kata". Idempotent. Run as root ON THE NODE. README.md (next to this file)
# has the why, the verification and the rollback.
#
#   bash install-kata.sh --yes         install / re-assert (restarts k3s only if the
#                                      containerd drop-in changed)
#   bash install-kata.sh --uninstall --yes   remove the drop-in + generated config, restart
#                                      k3s; keeps /opt/kata/<version> (delete it by hand)
#
# Uses an explicit containerd runtime drop-in:
#   /opt/kata/<version>/            the release's static bundle, extracted untouched
#   /etc/kata-containers/agent-array/configuration-<hv>-<runtime>-<version>.toml
#                                   the shim's config, generated from the bundle's own
#                                   configuration-<hv>.toml (paths moved under
#                                   /opt/kata/<version>, five overrides; see step 2)
#   .../containerd/config-v3.toml.d/20-kata.toml
#                                   containerd 2.x drop-in that k3s's generated config
#                                   already imports, so the k3s template stays untouched
#
# Knobs: KATA_VERSION, KATA_RUNTIME=rs|go (4.x; default rs),
# KATA_HYPERVISOR=clh|qemu (default clh), and K3S_UNIT (auto-detected).
set -euo pipefail
YES=0
UNINSTALL=0
for arg in "$@"; do
  case "$arg" in
    --yes) YES=1 ;;
    --uninstall) UNINSTALL=1 ;;
    *) echo "usage: $0 [--uninstall] --yes" >&2; exit 64 ;;
  esac
done
if (( ! YES )); then
  echo "Read-only plan: install/reconcile pinned Kata bundle and containerd drop-in; --yes required."
  exit 0
fi

VERSION="${KATA_VERSION:-3.32.0}"        # pinned; bump deliberately (README "Bumping")
RUNTIME="${KATA_RUNTIME:-rs}"
HV="${KATA_HYPERVISOR:-clh}"
HANDLER="kata"                           # runtimeclass-kata.yaml `handler:` must match

case "$(uname -m)" in
  x86_64) KARCH=amd64 ;;
  *) echo "only x86_64 is pinned and tested; add a pin for $(uname -m) first" >&2; exit 1 ;;
esac

# 3.x only has the Go runtime. Keep the old invocation compatible.
case "$VERSION" in 3.*) [ "$RUNTIME" = rs ] && RUNTIME=go ;; esac
case "$RUNTIME" in rs|go) ;; *) echo "KATA_RUNTIME must be rs or go (got '${RUNTIME}')" >&2; exit 1 ;; esac

# SHA256 pins reject replaced release assets; review provenance before updating.
case "${VERSION}/${KARCH}/${RUNTIME}" in
  3.32.0/amd64/go) PINNED_SHA256="1449ecea50bd91fa73a94648db195d18950fe869ba4b1f12d05f55f1fa7c1b01" ;;
  4.2.0/amd64/rs) PINNED_SHA256="b828904fa3f1e49ddd7dc799c72cb1503cd1e772d354c3987c8d4189b2a623a8" ;;
  4.2.0/amd64/go) PINNED_SHA256="7dda31ca54b397cbf8165f620d6041872d3c45b7a77383ce0e86f76a06e103d0" ;;
  *) echo "no pinned sha256 for Kata ${VERSION} on ${KARCH}; add it to $0 before installing" >&2; exit 1 ;;
esac
BUNDLE=kata-static
[ "$RUNTIME" = go ] && [ "${VERSION%%.*}" -ge 4 ] && BUNDLE=kata-go-static
TARBALL="${BUNDLE}-${VERSION}-${KARCH}.tar.zst"
URL="https://github.com/kata-containers/kata-containers/releases/download/${VERSION}/${TARBALL}"

case "$HV" in
  clh)  VMM_BIN="bin/cloud-hypervisor" ;;
  qemu) VMM_BIN="bin/qemu-system-x86_64" ;;
  *) echo "KATA_HYPERVISOR must be clh or qemu (got '${HV}')" >&2; exit 1 ;;
esac

KATA_ROOT=${KATA_ROOT:-/opt/kata}
DEST="${KATA_ROOT}/${VERSION}"
[ "${VERSION%%.*}" -ge 4 ] && DEST="${DEST}-${RUNTIME}"
MARKER="${DEST}/.agent-array-sha256"
CFG_DIR=${KATA_CFG_DIR:-/etc/kata-containers/agent-array}
CFG="${CFG_DIR}/configuration-${HV}-${RUNTIME}-${VERSION}.toml"
CONTAINERD_DIR=${KATA_CONTAINERD_DIR:-/var/lib/rancher/k3s/agent/etc/containerd}
DROPIN_DIR="${CONTAINERD_DIR}/config-v3.toml.d"
DROPIN="${DROPIN_DIR}/20-kata.toml"
# k3s on a server, k3s-agent on a worker; detected so a forgotten K3S_UNIT cannot leave
# kata configured but never loaded.
UNIT="${K3S_UNIT:-$(systemctl cat k3s.service >/dev/null 2>&1 && echo k3s || echo k3s-agent)}"

if [ "$RUNTIME" = rs ]; then
  SHIM_REL=runtime-rs/bin/containerd-shim-kata-v2
  CONFIG_REL="share/defaults/kata-containers/runtime-rs/configuration-${HV}-runtime-rs.toml"
else
  SHIM_REL=bin/containerd-shim-kata-v2
  CONFIG_REL="share/defaults/kata-containers/configuration-${HV}.toml"
fi

log() { printf '[install-kata] %s\n' "$*"; }
die() { printf '[install-kata] ERROR: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root"
if [ "${KATA_INSTALL_TEST_MODE:-0}" != 1 ]; then
  systemctl cat "${UNIT}.service" >/dev/null 2>&1 || die "no ${UNIT}.service on this box (set K3S_UNIT)"
fi

# Restart the k3s unit, then wait until its containerd reports the kata handler as
# present or absent ($1). Non-zero on timeout (~3 min).
restart_and_wait() {
  local want="$1" attempt info
  log "restarting ${UNIT} (running containers survive: KillMode=process)"
  systemctl restart "$UNIT" || return 1
  for ((attempt=0; attempt<90; attempt++)); do
    if systemctl is-active --quiet "$UNIT"; then
      info="$(k3s crictl info 2>/dev/null || true)"
      if [ -n "$info" ]; then
        if [ "$want" = present ] && grep -q "\"${HANDLER}\": {" <<<"$info"; then return 0; fi
        if [ "$want" = absent ] && ! grep -q "\"${HANDLER}\": {" <<<"$info"; then return 0; fi
      fi
    fi
    sleep 2
  done
  return 1
}

if (( UNINSTALL )); then
  if pgrep -f 'containerd-shim-kata-v2' >/dev/null; then
    die "kata sandboxes are still running (pgrep containerd-shim-kata-v2); delete those pods first"
  fi
  if [ -e "$DROPIN" ]; then
    rm -f "$DROPIN"
    restart_and_wait absent || die "${UNIT} did not come back without the kata handler; check: journalctl -u ${UNIT} -n 100"
  else
    log "no drop-in at ${DROPIN}; no restart"
  fi
  rm -rf "$CFG_DIR"
  rmdir /etc/kata-containers 2>/dev/null || true
  log "uninstalled. ${KATA_ROOT}/<version> trees are kept: rm -rf ${DEST} reclaims ~3.8 GB."
  log "Remove the runtime-kata node label; remove RuntimeClass only after all Kata workloads are drained"
  exit 0
fi

# ---- preflight --------------------------------------------------------------------
# Kata runs every pod as a KVM guest. Without /dev/kvm (no VT-x/AMD-V, disabled in the
# firmware, or a VM without nested virtualisation) it cannot work at all: refuse.
if [ "${KATA_INSTALL_TEST_MODE:-0}" != 1 ]; then
  [ -c /dev/kvm ] || die "/dev/kvm missing: no hardware virtualisation here. Kata needs KVM; refusing "
  ( exec 3<>/dev/kvm ) 2>/dev/null || die "/dev/kvm exists but cannot be opened (kvm module broken or blocked); refusing"
  [ -c /dev/net/tun ] || die "/dev/net/tun missing: Kata's pod networking needs tap devices"
  if [ "$HV" = qemu ]; then
    [ -c /dev/vhost-vsock ] || die "/dev/vhost-vsock missing: QEMU needs vhost-vsock (modprobe vhost_vsock)"
  fi
fi
grep -q 'config-v3.toml.d' "${CONTAINERD_DIR}/config.toml" \
  || die "this k3s does not import config-v3.toml.d; use the template method"
if [ "${KATA_INSTALL_TEST_MODE:-0}" != 1 ]; then
  command -v zstd >/dev/null || { die "zstd missing; install it through the node package-management process"; }
fi

# ---- 1. bundle -> /opt/kata/<version>[-<runtime>] -----------------------------------
if [ -d "$DEST" ]; then
  [ "$(cat "$MARKER" 2>/dev/null)" = "$PINNED_SHA256" ] \
    || die "${DEST} exists but was not installed from the pinned tarball (no/odd ${MARKER}); move it aside and re-run"
  log "bundle ${VERSION} already installed at ${DEST}"
else
  mkdir -p "$KATA_ROOT"
  # /var/tmp avoids exhausting hosts where /tmp is tmpfs; the tarball is large.
  avail_var="$(df --output=avail -BM /var/tmp | tail -1 | tr -dc 0-9)"
  avail_opt="$(df --output=avail -BM "$KATA_ROOT" | tail -1 | tr -dc 0-9)"
  [ "$avail_var" -ge 2000 ] || die "need ~2 GB free in /var/tmp for the download (have ${avail_var} MB)"
  [ "$avail_opt" -ge 6000 ] || die "need ~6 GB free under ${KATA_ROOT} (the bundle unpacks to ~3.8 GB; have ${avail_opt} MB)"
  tmp="$(mktemp -d -p /var/tmp kata-install.XXXXXX)"
  stage="${KATA_ROOT}/.${VERSION}.partial"
  trap 'rm -rf "$tmp" "$stage"' EXIT
  log "downloading ${URL}"
  curl -fsSL --proto '=https' --tlsv1.2 --retry 3 -o "${tmp}/${TARBALL}" "$URL"
  # The release ships no checksum file; the value pinned above is the reference.
  ( cd "$tmp" && printf '%s  %s\n' "$PINNED_SHA256" "$TARBALL" | sha256sum -c - )
  rm -rf "$stage"; mkdir -p "$stage"
  # Members are ./opt/kata/...; strip that prefix so the bundle lands in <version>/.
  tar --zstd -xf "${tmp}/${TARBALL}" -C "$stage" --strip-components=3 --no-same-owner ./opt/kata
  got="$(cat "${stage}/VERSION" 2>/dev/null || true)"
  [ "$got" = "$VERSION" ] || die "unpacked bundle reports VERSION '${got}', expected ${VERSION}"
  printf '%s\n' "$PINNED_SHA256" > "${stage}/.agent-array-sha256"
  chmod -R go-w "$stage"
  mv -T "$stage" "$DEST"          # atomic: DEST is either complete or absent
  rm -rf "$tmp"; trap - EXIT
  log "bundle ${VERSION} installed at ${DEST}"
fi
for f in "$SHIM_REL" "$VMM_BIN" libexec/virtiofsd \
         share/kata-containers/vmlinux.container \
         "$CONFIG_REL"; do
  [ -e "${DEST}/${f}" ] || die "bundle is missing ${f}"
done
# Go bundles carry a monolithic image; runtime-rs uses a composable Ubuntu image.
compgen -G "${DEST}/share/kata-containers/*.image" >/dev/null || die "bundle is missing a guest image"
"${DEST}/${SHIM_REL}" --version | head -1

# ---- 2. shim configuration ------------------------------------------------------------
# The bundle's config with every "/opt/kata/..." path moved to "/opt/kata/<version>/..."
# (the valid_*_paths allowlists too, so only this version's binaries can run), plus:
#   enable_annotations = []        no pod annotation may change hypervisor/kernel settings
#                                  (containerd forwards none either: pod_annotations below)
#   sandbox_cgroup_only = true     VMM, virtiofsd and shim run inside the POD's cgroup, so
#                                  the pod's limits + RuntimeClass overhead bound the whole
#                                  VM on the host (false parks them in an unconstrained
#                                  kata_overhead cgroup)
#   disable_guest_seccomp = false  the pod's seccomp profile is also enforced inside the
#                                  guest (defence in depth; privileged = unconfined anyway)
#   disable_guest_empty_dir = true disk emptyDirs stay in the kubelet's pod directory and
#                                  are shared in over virtio-fs. With the 3.x default
#                                  (false) the agent creates them inside the pause
#                                  container's snapshot instead, where the kubelet never
#                                  measures them: sizeLimit and ephemeral-storage limits
#                                  stop working and a pod can fill the node's root disk
#                                  (including control-plane storage). Upstream made true the default
#                                  right after 3.32.0 (kata-containers#12373, in 4.0).
#   reclaim_guest_freed_memory = true
#                                  virtio-balloon free page reporting: pages the guest frees
#                                  go back to the host. Without it the host keeps every page
#                                  the guest ever touched (guest RAM = 2 GiB + the limits),
#                                  page-cache churn pushes the pod cgroup to its limit, and
#                                  the host swaps or OOM-kills the VMM .
src="${DEST}/${CONFIG_REL}"
new="$(mktemp)"; trap 'rm -f "$new"' EXIT
{
  printf '# Generated by platform/kata/install-kata.sh from\n#   %s\n' "$src"
  printf '# (Kata %s, hypervisor %s). Do not edit: change the installer and re-run it.\n\n' "$VERSION" "$HV"
  sed -e "s#\"/opt/kata/#\"${DEST}/#g" "$src"
} > "$new"
set_key() {   # set_key KEY VALUE: the uncommented "KEY = ..." line must exist exactly once
  local n; n="$(grep -cE "^$1 = " "$new" || true)"
  [ "$n" = 1 ] || die "expected one '$1 = ' line in ${src}, found ${n}: upstream changed, review the overrides"
  sed -i -E "s|^$1 = .*|$1 = $2|" "$new"
}
set_key enable_annotations    '[]'
set_key sandbox_cgroup_only   'true'
set_key disable_guest_seccomp 'false'
set_key reclaim_guest_freed_memory 'true'
if [ "$RUNTIME" = go ]; then
  set_key disable_guest_empty_dir 'true'
else
  # runtime-rs replaced disable_guest_empty_dir with an explicit mode.
  set_key emptydir_mode '"shared-fs"'
fi
if grep -E '^[^#]*"/opt/kata/' "$new" | grep -vqF "\"${DEST}/"; then
  die "generated config still references the unversioned /opt/kata tree"
fi
mkdir -p "$CFG_DIR"
if cmp -s "$new" "$CFG"; then
  log "shim config already current: ${CFG}"
else
  install -m 0644 "$new" "$CFG"
  log "shim config written: ${CFG} (read at each sandbox start; no restart needed for it)"
fi
rm -f "$new"; trap - EXIT

# ---- 3. containerd drop-in --------------------------------------------------------------
want="version = 3

# Kata Containers ${VERSION} (${RUNTIME}/${HV}). Written by platform/kata/install-kata.sh; do not edit.
[plugins.\"io.containerd.cri.v1.runtime\".containerd.runtimes.${HANDLER}]
  runtime_type = \"io.containerd.kata.v2\"
  runtime_path = \"${DEST}/${SHIM_REL}\"
  # privileged: true in a kata pod gets NO host device nodes. \"Privileged\" then means
  # root over the guest kernel and the guest's virtual devices only.
  privileged_without_host_devices = true
  # Forward no pod/container annotations to the shim: a pod spec cannot override the
  # hypervisor, kernel, virtiofsd arguments or any other io.katacontainers.* setting.
  pod_annotations = []
  container_annotations = []

[plugins.\"io.containerd.cri.v1.runtime\".containerd.runtimes.${HANDLER}.options]
  ConfigPath = \"${CFG}\""
mkdir -p "$DROPIN_DIR"
if [ "$(cat "$DROPIN" 2>/dev/null)" = "$want" ]; then
  log "containerd drop-in already current: ${DROPIN}; no restart"
else
  prev=""; had_prev=0
  if [ -e "$DROPIN" ]; then prev="$(cat "$DROPIN")"; had_prev=1; fi
  printf '%s\n' "$want" > "$DROPIN"
  log "containerd drop-in written: ${DROPIN}"
  if ! restart_and_wait present; then
    log "containerd did not report the '${HANDLER}' handler; rolling the drop-in back"
    # A previous drop-in (a bump from an older version) registers the handler too, so
    # wait for it to be back; with no previous drop-in, wait for the handler to be gone.
    if [ "$had_prev" = 1 ]; then
      printf '%s\n' "$prev" > "$DROPIN"; restart_and_wait present || true
    else
      rm -f "$DROPIN"; restart_and_wait absent || true
    fi
    die "kata drop-in rolled back. Look at: journalctl -u ${UNIT} -n 200 | grep -i kata"
  fi
  log "containerd reports runtime handler '${HANDLER}'"
fi

log "OK. Next (from a kubeconfig box):"
log "  sync the rendered platform/kata RuntimeClass through Argo CD"
log "  label the node with the org LABEL_PREFIX/runtime-kata=true after verification"

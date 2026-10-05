#!/usr/bin/env bash
# Fake-node coverage for the 4.x runtime selection. No bundle or node is touched.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
installer=$here/../install-kata.sh
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
bin=$tmp/bin
mkdir -p "$bin"

cat >"$bin/id" <<'EOF'
#!/usr/bin/env bash
[[ ${1:-} == -u ]] && echo 0
EOF
cat >"$bin/systemctl" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat >"$bin/k3s" <<'EOF'
#!/usr/bin/env bash
printf '{"config":{"containerd":{"runtimes":{"kata": {}}}}}\n'
EOF
chmod +x "$bin"/*
export PATH="$bin:$PATH" KATA_INSTALL_TEST_MODE=1 K3S_UNIT=k3s
export KATA_ROOT=$tmp/opt/kata KATA_CFG_DIR=$tmp/etc/kata-containers/agent-array
export KATA_CONTAINERD_DIR=$tmp/containerd
mkdir -p "$KATA_CONTAINERD_DIR/config-v3.toml.d"
printf 'imports = ["config-v3.toml.d/*.toml"]\n' >"$KATA_CONTAINERD_DIR/config.toml"

make_bundle() {
  local runtime=$1 dest cfg shim digest
  dest=$KATA_ROOT/4.2.0-$runtime
  if [[ $runtime == rs ]]; then
    cfg=share/defaults/kata-containers/runtime-rs/configuration-clh-runtime-rs.toml
    shim=runtime-rs/bin/containerd-shim-kata-v2
    digest=b828904fa3f1e49ddd7dc799c72cb1503cd1e772d354c3987c8d4189b2a623a8
  else
    cfg=share/defaults/kata-containers/configuration-clh.toml
    shim=bin/containerd-shim-kata-v2
    digest=7dda31ca54b397cbf8165f620d6041872d3c45b7a77383ce0e86f76a06e103d0
  fi
  mkdir -p "$dest/$(dirname "$cfg")" "$dest/$(dirname "$shim")" \
    "$dest/bin" "$dest/libexec" "$dest/share/kata-containers"
  printf '#!/usr/bin/env bash\necho kata fake\n' >"$dest/$shim"
  chmod +x "$dest/$shim"
  : >"$dest/bin/cloud-hypervisor"; : >"$dest/libexec/virtiofsd"
  : >"$dest/share/kata-containers/vmlinux.container"
  : >"$dest/share/kata-containers/kata-ubuntu-latest.image"
  cat >"$dest/$cfg" <<'EOF'
path = "/opt/kata/bin/cloud-hypervisor"
kernel = "/opt/kata/share/kata-containers/vmlinux.container"
enable_annotations = ["kernel_params"]
sandbox_cgroup_only = false
disable_guest_seccomp = true
reclaim_guest_freed_memory = false
disable_guest_empty_dir = false
emptydir_mode = "block-plain"
EOF
  printf '%s\n' "$digest" >"$dest/.agent-array-sha256"
}

fail=0
ok() { printf 'ok   %s\n' "$1"; }
check() { local d=$1; shift; if "$@"; then ok "$d"; else printf 'FAIL %s\n' "$d" >&2; fail=$((fail + 1)); fi; }

make_bundle rs
KATA_VERSION=4.2.0 KATA_RUNTIME=rs bash "$installer" --yes >/dev/null
drop=$KATA_CONTAINERD_DIR/config-v3.toml.d/20-kata.toml
cfg=$KATA_CFG_DIR/configuration-clh-rs-4.2.0.toml
check 'runtime-rs shim path reaches the versioned rs tree' grep -Fq \
  "$KATA_ROOT/4.2.0-rs/runtime-rs/bin/containerd-shim-kata-v2" "$drop"
check 'runtime-rs source config path is selected' grep -Fq 'configuration-clh-runtime-rs.toml' "$cfg"
check 'runtime-rs keeps kubelet-accounted emptyDirs' grep -qx 'emptydir_mode = "shared-fs"' "$cfg"
check 'runtime-rs paths are versioned' bash -c '! grep -Eq "^[^#]*\"/opt/kata/" "$1"' _ "$cfg"
check 'containerd forwards no annotations' grep -Fq 'pod_annotations = []' "$drop"

make_bundle go
KATA_VERSION=4.2.0 KATA_RUNTIME=go bash "$installer" --yes >/dev/null
cfg=$KATA_CFG_DIR/configuration-clh-go-4.2.0.toml
check 'Go shim path reaches the separate go tree' grep -Fq \
  "$KATA_ROOT/4.2.0-go/bin/containerd-shim-kata-v2" "$drop"
check 'Go source config path is selected' grep -Fq 'configuration-clh.toml' "$cfg"
check 'Go emptyDir safety override remains enabled' grep -qx 'disable_guest_empty_dir = true' "$cfg"

set +e
out=$(KATA_VERSION=4.2.0 KATA_RUNTIME=bogus bash "$installer" --yes 2>&1); rc=$?
set -e
check 'invalid runtime fails before touching the node' test "$rc" -ne 0
check 'invalid runtime explains the accepted values' grep -Fq 'KATA_RUNTIME must be rs or go' <<<"$out"
check 'runtime-rs uses the kata-static bundle name' grep -Fq 'TARBALL="${BUNDLE}-${VERSION}-${KARCH}.tar.zst"' "$installer"
check 'Go 4.x selects the kata-go-static bundle' grep -Fq 'BUNDLE=kata-go-static' "$installer"

check 'installer parses' bash -n "$installer"
check 'installer has LF line endings' bash -c '! grep -q $'"'"'\r'"'"' "$1"' _ "$installer"
if (( fail )); then printf 'FAIL: %d checks failed\n' "$fail" >&2; exit 1; fi
printf 'PASS: 14 checks (4.2 bundles, runtime-rs and Go paths, configs, safety overrides, failure path)\n'

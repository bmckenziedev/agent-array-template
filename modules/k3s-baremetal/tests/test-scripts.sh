#!/usr/bin/env bash
set -euo pipefail
base=$(cd "$(dirname "$0")/.."; pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"
export PATH="$tmp/bin:$PATH" AA_STUB_LOG="$tmp/calls"
cat > "$tmp/cluster.env" <<'EOF'
K8S_VERSION=v1.35.1+k3s1
OVERLAY_KIND=tailscale
APISERVER_URL=https://100.64.0.10:6443
POD_CIDR=10.42.0.0/16
SERVICE_CIDR=10.43.0.0/16
OVERLAY_CIDR=100.64.0.0/10
TS_TAG_NODES=tag:k8s-node
SSH_PORT=22
FIREWALL_PROFILE=overlay
OVERLAY_INTERFACE=tailscale0
LABEL_PREFIX=example.org
BOOTSTRAP_SERVER_IP=100.64.0.10
EOF
cat > "$tmp/node.env" <<'EOF'
NODE_NAME=node-a
NODE_OVERLAY_IP=100.64.0.10
NODE_ROLES_JSON='["control-plane","sessions"]'
NODE_RUNTIME_CLASSES_JSON='["kata"]'
NODE_LABELS_JSON='{}'
EOF
cat > "$tmp/bin/id" <<'EOF'
#!/usr/bin/env bash
echo "${FAKE_UID:-1000}"
EOF
cat > "$tmp/bin/tailscale" <<'EOF'
#!/usr/bin/env bash
echo "$*" >> "$AA_STUB_LOG"
if [[ $1 == status ]]; then echo '{"BackendState":"Running"}'; fi
EOF
cat > "$tmp/bin/k3s" <<'EOF'
#!/usr/bin/env bash
echo 'k3s version v1.35.1+k3s1 (test)'
EOF
for command in ufw curl sha256sum install systemctl; do
  cat > "$tmp/bin/$command" <<'EOF'
#!/usr/bin/env bash
echo "$0 $*" >> "$AA_STUB_LOG"
exit 99
EOF
done
chmod +x "$tmp/bin/"*
refuse() {
  if bash "$base/$1" "$tmp/cluster.env" "$tmp/node.env" --yes > "$tmp/out" 2>&1; then
    echo "expected refusal: $1" >&2; exit 1
  fi
  grep -q "$2" "$tmp/out"
}
if bash "$base/10-stage.sh" "$tmp/missing" "$tmp/node.env" > "$tmp/out" 2>&1; then exit 1; fi
grep -q 'missing env file' "$tmp/out"
refuse 30-install.sh 'root required'
bash "$base/40-firewall.sh" "$tmp/cluster.env" "$tmp/node.env" > "$tmp/out"
grep -q 'ufw allow 22/tcp' "$tmp/out"
[[ ! -e "$tmp/calls" ]]
FAKE_UID=0 refuse 40-firewall.sh AA_SSH_CONFIRMED
FAKE_UID=0 bash "$base/30-install.sh" "$tmp/cluster.env" "$tmp/node.env" --yes > "$tmp/out"
grep -q 'already at pinned version' "$tmp/out"
bash "$base/20-overlay.sh" "$tmp/cluster.env" "$tmp/node.env" > "$tmp/out"
grep -q 'already running' "$tmp/out"
[[ $(wc -l < "$tmp/calls") == 1 ]]
# Hide the installed k3s stub, leaving no real cluster executable in the test.
rm "$tmp/bin/k3s"
bash "$base/30-install.sh" "$tmp/cluster.env" "$tmp/node.env" > "$tmp/out"
grep -q 'verify SHA256' "$tmp/out"
grep -q 'no token in argv' "$tmp/out"
# Root/file stubs pass custody checks but checksum verification must stop host writes.
cat > "$tmp/bin/stat" <<'EOF'
#!/usr/bin/env bash
if [[ $2 == %a ]]; then echo 600; else echo 0; fi
EOF
cat > "$tmp/bin/curl" <<'EOF'
#!/usr/bin/env bash
while [[ $# -gt 0 ]]; do
  if [[ $1 == -o ]]; then shift; target=$1; fi
  shift
done
if [[ $target == *sha256sum* ]]; then
  echo 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa  k3s' > "$target"
else echo 'fake binary' > "$target"; fi
EOF
cat > "$tmp/bin/sha256sum" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
chmod +x "$tmp/bin/"*
echo 'synthetic-token' > "$tmp/token"
export K3S_TOKEN_FILE="$tmp/token"
FAKE_UID=0 refuse 30-install.sh 'checksum mismatch'
[[ $(wc -l < "$tmp/calls") == 1 ]]
echo 'k3s-baremetal: 8 cases passed'

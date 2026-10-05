#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"
load_env "$@"
role=agent
server_role && role=server
if command -v k3s >/dev/null; then
  installed=$(k3s --version | head -n 1 | awk '{print $3}')
  [[ $installed == "$K8S_VERSION" ]] || die 'installed version differs; use reviewed maintenance upgrade'
  echo 'k3s already at pinned version; no installation changes'; exit 0
fi
[[ ! -e /etc/rancher/k3s/config.yaml.d/10-node.yaml ]] || die 'existing node config requires reviewed reconciliation'
[[ ! -e /etc/systemd/system/k3s.service ]] || die 'existing systemd unit requires review'
arch=$(uname -m)
case "$arch" in x86_64) asset=k3s; sums=sha256sum-amd64.txt ;; aarch64) asset=k3s-arm64; sums=sha256sum-arm64.txt ;; *) die 'unsupported architecture' ;; esac
url="https://github.com/k3s-io/k3s/releases/download/$K8S_VERSION"
echo "Download $url/$asset and $url/$sums; verify SHA256 before installing."
echo "Configure $role with node-ip=$NODE_OVERLAY_IP and a 0600 token-file; no token in argv."
if ! $yes; then exit 0; fi
secure_file "${K3S_TOKEN_FILE:?set K3S_TOKEN_FILE}"
: "${APISERVER_URL:?missing API URL}" "${POD_CIDR:?missing pod CIDR}" "${SERVICE_CIDR:?missing service CIDR}"
: "${LABEL_PREFIX:?missing LABEL_PREFIX}" "${NODE_RUNTIME_CLASSES_JSON:?missing runtime classes}"
: "${NODE_LABELS_JSON:?missing labels}" "${OVERLAY_INTERFACE:?missing overlay interface}"
if server_role; then : "${BOOTSTRAP_SERVER_IP:?missing bootstrap server IP}"; fi
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
curl --fail --location --proto '=https' --tlsv1.2 "$url/$asset" -o "$tmp/$asset"
curl --fail --location --proto '=https' --tlsv1.2 "$url/$sums" -o "$tmp/$sums"
awk -v asset="$asset" '$2 == asset || $2 == "*"asset {print $1 "  " asset}' "$tmp/$sums" > "$tmp/checksums"
[[ $(wc -l < "$tmp/checksums") == 1 ]] || die 'missing or ambiguous checksum'
(cd "$tmp"; sha256sum -c checksums) || die 'checksum mismatch'
install -m 0755 "$tmp/$asset" /usr/local/bin/k3s
install -d -m 0755 /etc/rancher/k3s/config.yaml.d
install -m 0600 "$K3S_TOKEN_FILE" /etc/rancher/k3s/join-token
config=/etc/rancher/k3s/config.yaml.d/10-node.yaml
if [[ -e $config ]]; then die 'existing node config requires reviewed reconciliation'; fi
umask 077
{
  printf 'node-name: "%s"\nnode-ip: "%s"\ntoken-file: /etc/rancher/k3s/join-token\n' "$NODE_NAME" "$NODE_OVERLAY_IP"
if server_role; then
    api_host=$(python -c 'import sys, urllib.parse; print(urllib.parse.urlparse(sys.argv[1]).hostname)' "$APISERVER_URL")
    printf 'bind-address: "%s"\nadvertise-address: "%s"\ntls-san:\n  - "%s"\n' "$NODE_OVERLAY_IP" "$NODE_OVERLAY_IP" "$api_host"
    printf 'cluster-cidr: "%s"\nservice-cidr: "%s"\nwrite-kubeconfig-mode: "0600"\n' "$POD_CIDR" "$SERVICE_CIDR"
    if [[ $NODE_OVERLAY_IP == "${BOOTSTRAP_SERVER_IP:?set first API endpoint to bootstrap server IP}" ]]; then
      printf 'cluster-init: true\n'
    else printf 'server: "%s"\n' "$APISERVER_URL"; fi
  else printf 'server: "%s"\n' "$APISERVER_URL"; fi
  if [[ $OVERLAY_KIND == tailscale ]]; then printf 'flannel-iface: "%s"\n' "$OVERLAY_INTERFACE"; fi
  # Register GPU taints immediately, before any ordinary workload can schedule.
  python - "$LABEL_PREFIX" "$NODE_ROLES_JSON" "$NODE_RUNTIME_CLASSES_JSON" "$NODE_LABELS_JSON" <<'PY'
import json
import sys
prefix, roles, runtimes, extra = sys.argv[1:]
roles = json.loads(roles)
runtimes = json.loads(runtimes)
labels = {prefix + '/role-' + role: 'true' for role in roles}
labels.update({prefix + '/runtime-' + runtime: 'true' for runtime in runtimes})
labels.update({k: str(v) for k, v in json.loads(extra).items() if k.startswith(prefix + '/')})
print('node-label:')
for key, value in sorted(labels.items()):
    print('  - ' + json.dumps(key + '=' + value))
if 'gpu' in roles:
    print('node-taint:\n  - ' + json.dumps(prefix + '/gpu=true:NoSchedule'))
PY
} > "$config"
unit=/etc/systemd/system/k3s.service
[[ ! -e $unit ]] || die 'existing systemd unit requires review'
cat > "$unit" <<EOF
[Unit]
Description=Kubernetes node
After=network-online.target
Wants=network-online.target
[Service]
Type=notify
ExecStart=/usr/local/bin/k3s $role
KillMode=process
Delegate=yes
Restart=on-failure
LimitNOFILE=1048576
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now k3s

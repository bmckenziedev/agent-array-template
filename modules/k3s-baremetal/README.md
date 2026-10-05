# Module: k3s-baremetal (optional)

This optional implementation stages self-hosted Linux nodes for the cluster contract; managed clusters do not use it.

## Interface

Numbered scripts take trusted rendered `cluster.env node.env [--yes]`. Default operation prints plans; --yes requires root and makes changes. Commands run locally on the staged node, with no automatic SSH orchestration. Stage OIDC and encrypted login roots separately, establish the overlay, install k3s, add firewall rules, apply the node contract, then verify. Daily API access uses OIDC; the bootstrap-only helper describes offline escrow without printing credentials.

## Configuration

Enable org.modules.k3s-baremetal.enabled. Defaults: ssh_user platform, ssh_port 22, firewall_profile overlay, overlay_interface tailscale0. Rendered module keys are M_K3S_BAREMETAL_*; global inputs are K8S_VERSION, API URL/port, pod/Service/overlay CIDRs, OVERLAY_KIND and TS_TAG_NODES. Per-node inputs are node name/overlay IP/roles. SSH_USER is inventory metadata for the external staging transport. The first API endpoint IP identifies the bootstrap server, which initializes embedded etcd. Other control-plane nodes join APISERVER_URL as servers using the same token. Bootstrap that endpoint first; use an odd-numbered healthy datastore quorum and review load-balancer/HA behavior (VERIFY). A load-balancer IP must not replace the bootstrap backend in the first endpoint entry.

## Secrets

K3S_TOKEN_FILE and TS_AUTHKEY_FILE name root-owned 0600 regular files. No token/auth-key value enters argv, output or Git. Tailscale reads its auth key using a file reference. Restrict custody of the join token; it protects bootstrap datastore material. Source env files only after review because shell source executes code.

## Deploy

Provision a supported OS and configure signed package repositories. 20-overlay installs the Tailscale package via the configured signed apt repository and brings it up only for OVERLAY_KIND=tailscale; other overlays are provisioned by the org. 30-install fetches a pinned k3s release binary/checksum over HTTPS, verifies checksum before installation, writes root-only token/config files and a systemd service, and binds the server API/node networking to overlay addresses. Stage the rendered OIDC drop-in before the first start. HTTPS release checksums provide transport integrity; a stronger supply-chain policy should validate release signatures before deployment. No curl-to-shell installer is executed.

40-firewall adds SSH first and role-scoped overlay/CNI allow rules; it never resets rules, changes defaults or enables UFW. Execution requires AA_SSH_CONFIRMED=yes after a second SSH session is tested. Printed rollback commands remove only additions, but review pre-existing equivalent rules before deleting them. Overlay role profiles allow kubelet TCP 10250 on all nodes, API/datastore TCP 6443/2379/2380 only on servers, and flannel VXLAN UDP 8472 within the configured overlay CIDR; pod-to-host TCP allowances use cni0. VERIFY different CNI transport/interface requirements. Tailnet ACLs limit peers and may accept traffic before UFW, so they are a required boundary. Server/agent roles determine k3s installation; the external upstream firewall must separately allow the correct role ports (API server TCP 6443, kubelet TCP 10250, chosen CNI transport and overlay handshake). No public API rule is added.

## Verify

Run `bash modules/k3s-baremetal/tests/test-scripts.sh`. Stubs cover dry runs, missing env, root refusal, checksum failure and pinned-install idempotency without contacting a host. After node install, 60-verify prints checks and executes read-only checks only with --yes. Also test OIDC, runtime isolation, node contract, API egress and vendor egress after changes.

## Rollback

Use printed firewall deletion commands after checking rule provenance. Stop the service and restore reviewed config/binary under maintenance; drain before removal. Join tokens are revoked/rotated by the platform custody procedure. No uninstall script automatically deletes node data.

## Security notes

See [network gotchas](GOTCHAS.md). Scripts do not transfer bootstrap kubeconfigs to laptops. Binary checksum mismatch fails before host changes. Existing installation upgrades are refused rather than silently upgraded. The installed-version idempotency path does not reconcile changed config; use maintenance review for configuration changes.

# Cluster contract

The platform accepts managed or self-hosted conformant Kubernetes. The optional bare-metal module is one implementation; node names and a particular overlay are not prerequisites.

## Interface

Kubernetes must provide ValidatingAdmissionPolicy GA (1.30 or newer), PodSecurity admission, a NetworkPolicy-enforcing CNI, RuntimeClass, and a Kata-capable session node pool with hardware virtualization and configured containerd handlers. k3s is pinned to `K8S_VERSION` in org.yaml. A conformant alternative must pass the same admission, runtime, identity and network tests. Install the distribution before platform manifests.

Every node carries `<label-prefix>/role-<role>=true` for each configured role: control-plane, worker, sessions, factory, gpu, ci. It carries `<label-prefix>/runtime-<class>=true` for every installed runtime class. GPU nodes carry `<label-prefix>/gpu=true:NoSchedule`. Runtime labels attest to installed, verified handlers, not desired future capability. Consumers select these labels; hostname placement is allowed only for a login belonging to a specific (user, tool, node).

`node-contract/labels.per-node.tmpl.json` records roles, runtimes and owned custom labels; `apply-node-contract.py --rendered rendered` expands the contract into label/taint commands. Add `--yes` to execute. Existing unrelated labels/taints are never removed. Role removal requires a reviewed drain and manual removal of stale owned labels/taints; the script is additive.

`STORAGE_CLASS_DEFAULT` is the org's normal workload storage. `STORAGE_CLASS_LOGIN` is a dedicated Retain local-path class rooted at `LOGIN_HOST_ROOT` on encrypted session nodes. These classes must differ. No shared/NFS login store and no backups of login roots. Managed node replacement requires fresh holder login rather than credential migration.

## Configuration

Consumes cluster distribution/version/provider/CNI, API URL/port/endpoint IPs/Service IP, pod/Service/overlay/private CIDRs, storage classes/login root, RuntimeClass names, node roles/runtime classes/labels, label prefix, namespaces and OIDC keys from org.yaml. No new global keys are defined. API endpoint lists must include every backend behind a load balancer and be updated before addresses change. Enable PodSecurity and validate policies before admitting sessions.

| Distribution | Identity configuration |
|---|---|
| k3s | Install [OIDC drop-in](oidc/k3s-oidc.tmpl.yaml) on every server, restart through maintenance; pinned `K8S_VERSION` |
| kubeadm | Equivalent `--oidc-issuer-url`, `--oidc-client-id`, `--oidc-username-claim`, `--oidc-username-prefix`, `--oidc-groups-claim`, `--oidc-groups-prefix` in kube-apiserver static Pod configuration |
| EKS | Associate external OIDC identity provider or use IAM access entries and exec credentials; VERIFY subject/group mapping, endpoint reachability and supported cluster version |
| GKE | Workforce Identity Federation / supported identity gateway and provider exec credentials; VERIFY Kubernetes username/group mapping and Kata node support |
| AKS | Managed Entra integration with kubelogin exec credentials; VERIFY group claims, RBAC mode and supported Kata node pool |

Managed IAM/Entra paths do not automatically expose the external OIDC subjects used by sessions. Either configure an identity bridge that preserves the subject/group contract or adapt org identities and the aa login implementation together. A managed offering without compatible Kata nodes does not satisfy the session contract.

### API-server NetworkPolicy lesson

Default-deny namespaces need egress to both the API Service IP on TCP 443 and all actual API endpoints on `APISERVER_PORT`. kube-router evaluates after DNAT, so an allow to the Service alone can silently fail. Calico's iptables/eBPF modes and Cilium's service translation differ: VERIFY the enforcement point with the installed version. Cilium may additionally require its kube-apiserver entity policy. Flannel alone does not enforce NetworkPolicy; k3s needs its kube-router policy controller or a replacement CNI. Test TokenReview and API access from the actual restricted namespace, and confirm a disallowed endpoint is blocked. Re-run after endpoint/node IP changes.

## Secrets

OIDC issuer/client ID and group names are public configuration. Keep the bootstrap admin certificate and k3s join token in offline/host-only custody; no static admin kubeconfigs on laptops. Daily access uses [OIDC](oidc/README.md).

## Deploy

Render reviewed org files, prepare encrypted login roots, apply the node contract, then manually sync the wave 08 cluster app after wave 00 namespace hardening. See [login storage](login-storage/README.md) for the system namespace PSA exception. Platform admins must verify session exec-guard admission before user access is enabled.

## Verify

Run `python -m unittest discover -s cluster/tests -t cluster` and `bash cluster/tests/test-prepare-login-root.sh`. Live: confirm node labels/taints, Kata VM isolation, PSA denials, API egress, login node affinity, and OIDC audit subjects. No live actions run in export tests.

## Rollback

Revert reviewed manifests and sync manually. Do not prune retained PVs. Drain nodes before removing owned role/runtime labels. Retain offline bootstrap access during identity changes.

## Security notes

[Identity RBAC](rbac/README.md) relies on session admission for exec restrictions. Labels are scheduling assertions, not a security boundary; only platform admins may modify node contracts. Encryption must be verified on the actual device ancestry; the login prep script changes nothing without `--yes`.

Login storage runs in the dedicated project login-storage namespace, with privileged enforcement and restricted warnings/audit. Local-path helpers require hostPath restricted to LOGIN_HOST_ROOT by host-access admission.

Adoption verifies encrypted login-root ancestry, local-path helper permissions and retention, managed-provider subject/group mapping, and auditor discovery coverage. Preserve executable modes for shell helpers; apply only reviewed node/runtime labels.

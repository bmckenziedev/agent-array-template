# Platform hardening

Namespace ownership, PSA, default-deny networking and guarded admission are generated from the organisation model. Argo manages rendered manifests; this component is the sole owner of Namespace objects for org namespace keys and Kubernetes built-in namespaces.

## Interface

`render_plugin.py` exposes `PLUGIN_NAME = "hardening"` and `render(model, emit)`. It emits Namespace and NetworkPolicy objects beneath `global/platform/hardening/k8s/`. Static admission and repo-server templates render beside them. Per-user namespaces belong to sessions; optional module namespaces belong to their module.

## Configuration

`org.namespaces` supplies every namespace except `user_prefix`. Built-ins are added explicitly. Namespace names must be unique. `components.hardening` deep-merges the defaults in `org.component.defaults.yaml`: `psa`, `psa_version` (latest), and `apiserver_clients` (system, mcp, portal, factory, ops, monitoring, argocd). A new namespace key requires an explicit PSA table entry; unknown client refs fail closed.

| Namespace reference | Enforce | Warn / audit |
|---|---|---|
| system, mcp, portal, factory, egress, session_jobs, ops, argocd | restricted | restricted |
| kube-public, kube-node-lease | restricted | restricted |
| llm, models, kube-system, default | baseline | restricted |
| monitoring | privileged | restricted |

Every namespace has enforce-version, warn-version and audit-version. The llm baseline accommodates Postgres/Redis image shapes; models accommodates GPU lanes. Monitoring's host access requires privileged PSA and the narrow node-exporter admission exception. Restricted PSA broke CI runner shapes: optional ARC modules must choose their own justified namespace levels and VM isolation.

Consumed keys: `PROJECT_NAME`, `LABEL_PREFIX`, `NS_MONITORING`, `NS_ARGOCD`, `APISERVER_ENDPOINT_IPS_JSON`, `APISERVER_PORT`, `APISERVER_SERVICE_IP`, `CLUSTER_DNS_IP`. Defaults are consumed directly by the plugin; the renderer also publishes corresponding `C_HARDENING_*` keys.

## Secrets

None. No credentials or certificate material are shipped.

## Deploy

Render and commit the output, then use a manual-sync Argo Application targeting `rendered/global/platform/hardening` with directory recursion and `*.yaml` inclusion. The cluster task owns that Application and its least-privilege AppProject. Do not enable namespace pruning or attach a resources finalizer to namespace ownership. Before enabling Deny bindings, review API-server type checking and run the documented positive cases in a staging namespace with equivalent PSA. Admission bindings are namespace-scoped using the built-in namespace-name label; this covers all namespace names, including module and future app namespaces. Host-access match conditions limit enforcement to monitoring or its operator identity.

The repo-server policy permits IPv4 public TCP 22/443 only, excluding private, overlay, loopback, link-local, documentation, multicast and reserved ranges. Private Git needs an explicit reviewed policy. IPv6 Git remains denied. DNS helper policies permit only selected cluster DNS pods and the configured DNS service IP, TCP/UDP 53; kube-system also receives DNS ingress.

## Verify

Install `requirements-test.txt` in a disposable environment, then run:

```sh
python -B -m unittest discover -s platform/hardening/tests -t platform/hardening -v
```

Tests parse every template with fixture keys, validate per-namespace output and evaluate monitoring CEL offline. `cel_guards.py` checks optional field reads for preceding has() guards; it is a structural lint, supplemented by actual CEL evaluation and API-server checks. It does not prove arbitrary CEL logic.

A platform admin runs the shared live harness after syncing policies:

```sh
python platform/hardening/tests/live/check_admission.py \
  --kubeconfig ~/.kube/cluster.yaml \
  --rendered rendered/global/platform/hardening \
  --admin-user '<oidc-subject>' --admin-group '<oidc-platform-admin-group>'
```

It first verifies installed policies match rendered specs, then server-dry-runs plain busybox:1.36 Pod and Deployment positives under both Argo and platform-admin impersonation, before any negative cases. An RBAC or schema error does not count as a policy denial. No object is persisted. Ephemeral-container restrictions are covered offline; live subresource verification needs an existing disposable Pod and is a separate platform-admin check.

Test NetworkPolicies by restarting a disposable pod: existing connections can hide a missing grant. Re-run API egress probes after node IP changes. VERIFY CNI policy behavior: kube-router evaluates after DNAT, so endpoint IPs plus the service IP are intentional. Cilium may use a reviewed `toEntities: kube-apiserver` policy; Calico pre/post-DNAT behavior must be verified. No CNI-specific CRD is required by default.

## Rollback

Revert rendered policy changes through Git and manual Argo sync. Recover connectivity with a narrowly scoped reviewed grant. Admission binding removal should use an audited break-glass process. Namespace deletion is never a policy rollback.

## Security notes

Every optional CEL field is guarded with has(). Missing initContainers, ports, securityContext or volumes must admit ordinary workloads instead of causing evaluation errors. The monitoring exception pins the image digest, controller identity, service account, UID/GID, read-only filesystem and three read-only host mounts. It never grants privileged containers or hostPort. PSA privileged alone is not a protective control.

The export includes no ARC service accounts. Audit attribution, Metadata-level secret/configmap events and SIEM enrichment for per-user namespaces (inventory D04) are responsibilities of the ops audit task. Runtime isolation, admission, network policy and host controls are complementary; IdP/MFA, image signing/SBOM, classification, incident response and SIEM retention remain organisation controls.

Kube-system enforces privileged PSA because k3s local-path helper pods and klipper svclb need host access. Warn/audit remain baseline; the host-access admission policy is the compensating control. Keep API endpoint IPs current. Disabling k3s network policy leaves stale iptables rules; kube-router does not protect the pod startup default-deny window (k3s issue 14711). Cilium uses toEntities kube-apiserver; Calico iptables requires post-DNAT endpoint addresses.

API endpoint rules match post-DNAT kube-router/Calico iptables traffic; the Service-IP
rule is harmless there and portable to pre-DNAT implementations. Cilium adds
kube-apiserver entity rules. Calico-native service selectors are an optional adoption
extension. Keep endpoint addresses synchronised with `kubectl get endpoints kubernetes`.
Disabling k3s network-policy leaves stale iptables rules requiring reviewed cleanup;
its embedded kube-router does not protect the pod-startup default-deny window.
See [k3s networking](https://docs.k3s.io/networking/networking-services) and
[the upstream startup-window issue](https://github.com/k3s-io/k3s/issues/14711).

Deploy positive-first admission probes before Deny. Reverify chart pod labels, runtime/KVM prerequisites and network probes after node-IP changes. Structural CEL lint is not a proof of arbitrary policy logic; host-root remains trusted.

# Threat model

This is the export's intended control contract, not a security certification. Demonstrate
enforcement at [pilot gates](ADOPTION.md); use [private disclosure](../SECURITY.md) for defects.

## Assets, actors and boundaries

Assets include seat logins, API keys, identity, sealing/backup keys, classified code,
task budgets, result integrity, GitOps policy and audit evidence. Actors include malicious
repo content/dependencies, compromised tenant tools, unauthorised users, compromised
controllers and privileged administrators. Vendors and artifact registries are external trust.

Boundaries include IdP/client, user/namespace, guest/host, service/upstream, Git/GitOps,
credential-bearing task service/untrusted tester and platform/vendor. Node-root,
cluster-root and recovery custodians can cross boundaries. Kata/RBAC do not eliminate
this authority; logs and backups need equivalent protection.

## Enforced controls

| Control | Purpose and verification |
|---|---|
| PSA | Restricted baseline, explicit documented exceptions and pinned levels; restricted warn/audit when relaxed |
| Admission | Namespace-label scope, Deny/Fail, every optional CEL read guarded by `has()`; positive/negative dry-runs before binding |
| Default-deny plus egress | No tenant ingress/Service; DNS, approved vendor and entitled peers only; API endpoint AND service-IP allowance where needed |
| Kata | Session guest boundary; verify actual runtime and placement rather than labels |
| Digest pins | Reviewed immutable artifacts; strict render rejects placeholder pins, admission rejects mutable refs |
| Sealed secrets | Names/recipes/ciphertext in reviewed Git, plaintext only in protected runtime/recovery custody |
| Fail-closed entrypoints | Reject missing/hash-mismatched managed policy, unsafe env/endpoint overrides and unmanaged MCP |
| No secrets in sessions | No Kubernetes Secret injection; private seat login and explicit MCP/pace projected audiences only; no API token automount |
| Exec guard | Holder-only CONNECT or ticketed break-glass; VERIFY support or validated webhook fallback |
| Audit | Actor/team/target/outcome metadata, without bodies/tokens; protected external retention needed |

Baseline positive server-dry-run fixtures include plain `busybox:1.36` Pod without
initContainers/ports/volumes and a plain Deployment in the intended baseline scope.
Session policies also require exact session shapes. Negatives cover extra containers,
Secret mounts, wrong token audiences, env/command overrides, foreign claims, mutable images
and foreign exec. Existing Ready pods prove nothing about new admission evaluations.

Some example vendor routes permit public HTTPS, broader than exact-host policy. Exclude
private/link-local/node-public addresses and prefer verified gateways when classification
requires them. Test IPv4/IPv6 and host paths; pod policy alone is insufficient.

## Organisation controls to add

Require IdP MFA/privileged lifecycle, protected SIEM retention, DLP/classification,
image signing/SBOM, registry/Git protection, vendor data/retention agreements and independent
penetration testing. Encrypt etcd and node volumes; separate recovery key custody;
use protected node labels and dedicated session nodes where assurance requires them.
Define RPO/RTO, incident authority and tested alert delivery. Review every relaxed PSA or
host-access exception. Tests cannot approve contractual handling of data.

## Generic known-risk register

Status describes design treatment, not a deployed proof. Validate generated components
and live behavior before closing an item; no private topology or raw audit evidence is shipped.

| Risk | Export mitigation status | Remaining gate |
|---|---|---|
| Host-access controllers reach node credentials | Partial: PSA and scoped controllers; host exceptions remain trusted | Restrict helper paths/SAs, enumerate mounts/capabilities and test host-access denial |
| Cluster-wide Secret readers escalate | Partial: namespaced access/least-privilege projects are required | Effective controller/sidecar/monitoring RBAC audit; remove unneeded Secret collectors |
| Shared admin credential lacks individual revocation | Design mitigation: OIDC/scoped roles replace daily shared admin | No distributed admin kubeconfigs; protected and revocable emergency access |
| Unencrypted etcd/snapshots expose Secrets | Open organisation gate; sealing does not encrypt live etcd | Enable encryption, prove re-encryption, retain recovery keys and encrypted backups |
| CI/session co-location increases host risk | Partial: sandbox runtimes and role placement | Dedicated production nodes/taints and negative credential/network tests |
| Kubelet-spoofable placement labels | Open cluster gate; ordinary project labels insufficient | NodeRestriction/protected labels and admission-required affinity or provider equivalent |
| Host INPUT/public paths bypass pod policy | Partial: endpoint exclusions and additive host controls | Verify host/upstream IPv4/IPv6 firewall, API/kubelet and hostNetwork paths |
| Argo local admin/broad projects bypass policy | Design mitigation: OIDC, disabled local admin, scoped projects/manual policy sync | Effective controller/project RBAC and tested emergency recovery |
| Wazuh privileges expose logins/backups | Optional/partial: scoped monitored paths, no login mounts | No root-wide mount/DAC credential access/hostPID; remote config/command and network limits |
| Task credentials reach arbitrary containers | Optional runner gate: exact container/command/mount shape and secret-free tester | Negative extra-container and forged task/controller tests |
| Mutable runner image changes credential-bearing code | Design mitigation: digest gates | Tag-only admission denial, signing/provenance review |
| Scanner exempts tokens containing fixture words | Input/sanitization gate; no broad substring exemptions | Real-looking tokens containing test/mock/sample remain blocked; add DLP |
| Concurrent API calls overshoot budgets | Partial: task budgets and routing; telemetry may lag | Atomic reservations/request ceilings where needed and vendor hard stops |
| Portal accepts broad proxy membership | Design mitigation: application identity/team checks | Empty/unknown membership and wrong issuer/audience fail closed; cross-task denial |

## Operational stance

Use [break-glass](runbooks/break-glass.md) for exceptional access. Contain incidents by
stopping affected workloads/routes while preserving metadata, then revoke credentials
without printing them. Cluster-root can remove admission; independent audit/change review
provides detection. Restore tested snapshots and require fresh seat login, never login backups.

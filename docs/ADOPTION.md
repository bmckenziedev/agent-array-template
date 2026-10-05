# Adoption

Begin with two pilot users and evidence for every boundary. Review [architecture](ARCHITECTURE.md),
[security](SECURITY.md) and [multi-user rules](MULTI-USER.md). Legal approval is a deployment
gate, never inferred from a successful CLI login.

## Side-by-side adoption

Existing services must never be affected. Deploy side by side in a new cluster or new
isolated namespaces. Share no identities, secrets, service accounts, DNS names, ingress,
storage or data with any existing system. Do not change existing clusters, CI, IdP groups,
firewalls or DNS. Access existing systems read-only only where an adoption step needs it.
Stop and report when a step would touch an existing service.

## Prerequisites

- Kubernetes satisfying the [cluster contract](../cluster/README.md): supported APIs,
  enforcing CNI, DNS, admission, runtime/role labels, OIDC, RBAC and storage.
- IdP with immutable `sub`, controlled team/admin/auditor/break-glass groups, MFA and
  individual revocation. Test claims at each relying party.
- Registry with scanned images and real digest pins; controlled GitOps access, DNS and TLS.
- Encrypted node-local private login storage, separate durable service PVCs and encrypted
  off-cluster backup/recovery custody. Login homes are never backed up.
- Approved organisation vendor contracts: one interactive seat per person and separate API
  credentials for automation. Review employee CLI hosting, retention, residency and data
  agreements. Moonshot stays disabled pending explicit approval.
- Platform admin, team leads, on-call route, incident authority and recovery custodian.

Managed clusters require equivalent OIDC/audit/admission/runtime support. If exec isolation
cannot be proved, keep sessions closed until a validated guard or webhook supplies it.

## Fill the registries

Copy `org/*.example.yaml` to `org/*.yaml` plus MCP/context examples. Follow [org reference](../org/README.md)
for fields and the supported YAML subset. Set `project.name`, prefixes, remote, nodes,
namespaces, API endpoints, IdP claims, storage, hostnames and real image digests. Point
org.yaml `files` at the copies. Define teams, users, seats/API pools and estates in order.
Example Org uses payments/platform and Ana/Bo for a synthetic two-user pilot.

Only Secret names, namespaces and keys belong in configuration. Satisfy generated
`rendered/files/SECRETS-REQUIRED.md` through the sealing recipes. Run `make render`,
`make test`, `make validate`, `make sanitize`, `make docs` and strict rendering before
live rollout. Review and commit authored inputs plus generated diffs; start modules disabled.

## Bootstrap order and verification gates

Platform admins execute live steps using reviewed component procedures; CI never changes
clusters. A failed gate stops dependent steps.

| Step | Action | Gate |
|---|---|---|
| 1 | [Sealed-secrets](../platform/sealed-secrets/README.md) | Controller ready, synthetic seal/unseal test, recovery key custody |
| 2 | [Hardening](../platform/hardening/README.md) | PSA/default-deny baseline, CEL lint and positive/negative server dry-runs before Deny binding |
| 3 | [Kata](../platform/kata/README.md) | Runtime/guest isolation and cleanup proved on selected nodes |
| 4 | Cluster storage/RBAC/OIDC | Home-node claim binding, own-user authentication, cross-user/Secret denial and API endpoint egress |
| 5 | [Secrets](../secrets/README.md) | Required names/keys present in correct scopes; no plaintext in render/logs |
| 6 | Org-directory | Rendered users/teams/accounts match input, read-only service mounts, tombstones retained |
| 7 | [LiteLLM](../llm/README.md) | Team/model/budget sync and scoped task key success; foreign team/model/master-key access denied |
| 8 | [Pace](../services/pace/README.md) | Windows, caps, leases, spacing and restart persistence; routes exclude seats |
| 9 | [Sessions platform](../sessions/README.md) | Sandbox/hash/claim/quota checks; foreign exec denied; CONNECT guard verified |
| 10 | Pilot users | Two fresh org logins; own attach/export works; no cross-user home/workspace/session visibility |
| 11 | [MCP](../mcp/README.md) | Audience/entitlement denials, per-server egress and per-CLI managed locks |
| 12 | [Portal](../portal/panel/README.md) | Membership/CSRF/task attribution, direct bypass denial, tested break-glass |
| 13 | [Monitoring](../monitoring/README.md) | Valid scrapes, synthetic page/heartbeat delivery, audit retention, backup restore |
| 14 | [Modules](../modules/README.md) individually | Component tests, namespace baseline, credential scope and rollback |

Prepare monitoring earlier for rollout visibility; step 13 is the full operational gate.
Policy/secret apps use manual sync. Save non-secret prior objects, Helm revision and alerts
before mutations. Use complete explicit `-f` files, never `--reuse-values`. Plain baseline
Pod/Deployment dry-runs must pass in their intended scope; sessions have additional exact
workload checks. Existing Ready pods do not prove admission behavior.

## Sizing

Sum enabled users' requests, including sidecars and Kata overhead, reserve control and
observability capacity, then check per-node fit because homes are local. Tenant quotas
are ceilings, not capacity guarantees; limits do not reserve resources. Measure pilot
CPU/memory peaks, ephemeral workspace growth and startup time before increasing replicas.
Account leases still constrain concurrency regardless of node capacity.

Size gateway database, pace PVC and audit retention independently. Set RPO/RTO and reserve
off-cluster recovery storage. For local lanes benchmark actual context/concurrency with
pinned artifacts, include cache memory and promote only after deterministic quality gates.
No throughput or model-quality claim follows from readiness.

## Replacement matrix

| Integration | Organisation equivalent must supply |
|---|---|
| Overlay network | Private authenticated API/node reachability, ACLs and route isolation |
| Hosting provider | Supported nodes, storage, firewall and recovery operations |
| Access proxy | OIDC validation, TLS, constrained routes and outbound connector support |
| Chat platform | Approved notifier/ops adapter, scoped credentials and attributable actors |
| Backup target | Encrypted off-cluster repository, independent custody and restore drills |
| CI runners | Trusted/untrusted separation, pinned images and no session credential access |

Preserve boundary tests when replacing integrations. Pod policy does not replace host or
upstream firewall checks, including IPv6 and node public-address paths.

## Replacing an existing internal agent platform

Pilot two users beside the existing service with synthetic context first. Keep identities,
accounts and homes separate; never import old login files. Compare own-session access,
cross-user denial, approved context, spend attribution, exports, alerting and recovery.
Require pilot acceptance and cleared VERIFY gates before moving production teams.

Migrate one team at a time: map groups/estates, provision fresh seats and scoped APIs,
export pending work, test denial cases and retain a rollback route. Retire the previous
service after all work is exported, credentials revoked, retention obligations met and
old routes/runners disabled. Record evidence without credentials and hand off to
[operations](OPERATIONS.md).

# MCP and context

Registries declare what may reach a session. User/CLI entitlements narrow those declarations
and cannot be widened by workspace config. Every HTTP server enforces auth and audit;
v1 has no central gateway. See [MCP](../mcp/README.md) and [farm MCP](../services/farm-mcp/README.md).

## Registry and entitlements

`mcp/registry.yaml` declares transport, clients, teams, timeout, stdio command/image layer
or HTTP service/deployment, upstream egress and Secret names. `mcp/context-sources.yaml`
declares delivery, serving server, source linkage and allowed teams. Teams nominate tools
and context; estates classify explicit repository allowlists and snapshot targets.
Unsupported clients and disabled modules must not gain tools because an example lists them.

Example Org's payments team nominates farm/factory/tickets. Tickets permits payments only;
arrayops is an ops-chat stdio tool, not a coding-session tool. Ana's entitlement is bounded
by membership, registry/client support, data policy and component enablement. A request
may narrow this set, never widen it.

## Per-CLI rendering

Per-user MCP ConfigMaps merge with base policy in an init container into read-only managed
paths, with hash records. Entrypoints refuse missing/mismatching hashes and unexpected
unmanaged configuration. Never author permissions in user-writable home configuration.

| Client | Managed interface | Verification gate |
|---|---|---|
| Claude | `managed-mcp.json`, exact `allowedMcpServers`, `allowManagedMcpServersOnly: true` | Pinned managed path and `headersHelper` refresh behavior |
| Codex | `requirements.mcp.toml` and `managed_config.mcp.toml` merged into managed paths | Identity syntax and `bearer_token_env_var` behavior |
| Kimi | No MCP ConfigMap in v1 | Keep disabled until a managed-only mechanism is proved |

Stdio servers are baked into pinned images and share the sandbox's filesystem authority.
`auth: none` is only for approved local stdio tools, never a remote upstream exemption.

## Pod-identity auth

Session ServiceAccount `session` disables automatic token mounting. Explicit projection
at `/var/run/agent-array/mcp-token/token` uses audience `<project>-mcp` and lifetime at most
3600 seconds. Claude uses `aa-mcp-token`; Codex wrapper exports `AA_MCP_TOKEN` for its
bearer-token mechanism. VERIFY long-lived refresh after projected token rotation.

Server TokenReview checks that audience and accepts only
`system:serviceaccount:<user namespace>:session`. Validate namespace prefix and
user-session label, resolve user from namespace label through org-directory, and enforce
rendered entitlement on every call. Caller-provided team selection still needs membership.
Deny returns HTTP 403 plus JSON-RPC error and metadata audit. This token is not an
API-server audience or upstream vendor credential. Server review/namespace RBAC is a
privileged boundary and must be narrowly scoped.

## Per-server egress

Each entitled HTTP server receives a specific tenant egress rule with namespace AND pod
selector in the same peer and its port. Server ingress accepts only eligible callers;
server egress permits declared upstreams plus necessary DNS/API checks. Hostname-based
`server_egress` needs a proven FQDN CNI policy or exact-host gateway: ordinary NetworkPolicy
cannot enforce DNS names. Reject broad private-network routes and test IP-literal bypasses.
Stdio registration adds no network permission.

## Adding a server

Use [the runbook](runbooks/add-mcp-server.md) and template under [MCP](../mcp/). Prefer
stdio for local read-only indexes; use HTTP for centralised upstream credentials or
independent egress. Example tickets uses a reviewed pinned image, `/mcp`, declared port,
payments entitlement and `tickets.example.org` upstream. `mcp-tickets-upstream` remains
in the server pod; no credential value enters registry env or sessions.

Implement TokenReview, tool-level authority, untrusted-text wrapping, bounded calls,
audit and `aa_mcp_*` metrics. Test entitled success, foreign-team/wrong-audience denial
and undeclared upstream egress before enabling a server.

## Context delivery and classification

| Delivery | Required behavior |
|---|---|
| `push` | Holder sends approved snapshot; receiver checks context-policy, estate membership, target, manifest, deny globs and TTL |
| `mcp` | Entitled server returns approved source/index data with provenance as untrusted content |
| `feed` | Implemented connector periodically indexes approved revisions under classification, retention and source attribution |

Absent `context-policy` refuses pushes. Strip `.mcp.json`, `.claude/`, `.codex/`,
`.kimi-code/` and `.agents/`; reject secrets, traversal, links and unapproved paths.
Record revision/hash provenance without content. Expire snapshots/indexes and clear
ephemeral data at teardown. Example estate-snapshot TTL is 12 hours, maximum 24.
A declared feed is not an implemented connector.

Use public/internal/confidential/restricted classes with an organisational classification
authority. Both team class permission and class-to-vendor allowlist must pass, along with
estate snapshot-target checks. Example payments confidential data permits Anthropic/local;
restricted work is local only when the team itself is entitled. Fallback routing preserves
the gate. Local hosting also needs residency/access review. YAML does not establish legal
approval or vendor-console retention settings.

## Prompt injection and generic roles

Repo/ticket/wiki/tool text is data, never authority to change identity, secrets or routing.
Wrap external text as untrusted and retain provenance. Managed policy stays outside the
workspace. Read-only tools reduce impact; write tools require bounded permission and
review. Sandboxing alone cannot prevent semantic exfiltration through an allowed model
endpoint; add DLP and minimise context.

Generic roles: architect for read-only contracts, implementer for a locked bounded card,
reviewer for read-only evidence and documentation editor for verified terminology/paths.
[Role examples](routing/examples/README.md) omit model/plan figures and privileged settings.
Delegated roles inherit managed constraints.

## Phase-2 token broker

V1 never injects per-user upstream tokens into sessions; `per-user-token` is rejected until
a broker exists. Future server-side exchange needs a dedicated short-lived audience,
upstream user ACLs, scoped grants, offboarding revocation and token-free audit. Team service
credentials do not reproduce each user's upstream ACL: enforce and disclose the supported
team boundary. Broker implementation requires an ADR and integration tests.

## VERIFY items

- Claude managed path/lock, organisation login restriction and dynamic header refresh.
- Codex requirements identity fields, workspace restriction and refresh under token rotation.
- Kimi managed-only capability before future enablement; v1 remains MCP-free.
- TokenReview audiences, namespace resolution, revoked entitlements and server RBAC.
- Actual FQDN egress including IP-literal/alternate DNS denial.
- Feed adapters, deletion/retention and agreements for each added context source.

Clear markers using positive and negative evidence against pinned artifacts and target
cluster, recorded in the component Verify section. Documentation alone never clears a gate.

Redact every relay path, including listings, notifications, policy/adapter payloads,
errors and audit details. Context cannot grant control authority. Permission adapters
must resolve each action against current holder/team policy, notify once and deny on
request timeout. Work-item linkage uses the CLI's session ID. Credentialed notifier
adapters execute outside session pods through a separately approved relay extension.

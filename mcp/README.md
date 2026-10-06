# MCP and context registry

The registries describe capabilities and context delivery. Team entitlements determine which
capabilities reach a user session. Configuration is managed, deterministic and free of
credential values; upstream credentials remain in server pods.

Decision record: [0017: MCP pod identity in v1](../docs/adr/0017-mcp-pod-identity-v1.md).

## Interface

`render_plugin.py` exposes `PLUGIN_NAME = "mcp"` and `render(model, emit)`. It consumes the
normalized organization model and emits Kubernetes manifests under `users/<slug>/mcp/`,
`global/mcp/` and `mcp/<server>/mcp/k8s/server/`. Offboarded users are omitted. Suspended users
retain configuration; HTTP authentication refuses suspended users.

Per tool, optional ConfigMaps are named `claude-mcp` and `codex-mcp`. Claude data keys are
`managed-mcp.json`, `allowed-mcp-servers.json` and `mcp-rendered.json`. Codex data keys are
`requirements.mcp.toml`, `managed_config.mcp.toml` and `mcp-rendered.json`. The last file has
`servers` and `sha256`, hashing the UTF-8 bytes of each other data key, including its trailing
newline. No ConfigMap is emitted when no server supports an enabled tool.

`context-policy` contains `estates.json` (an array of `{id, data_class, repos,
snapshot_targets, deny_globs}`) and `sources.json` (an array of source records). Estate ownership,
the owning team's data-class vendor policy and enabled user tools all constrain snapshot targets.
Global deny globs are included in every estate entry. The snapshot receiver must refuse all
pushes if the policy is absent. Each entitled HTTP server receives a `mcp-egress-<server>` policy
in the user's namespace, selecting the user's pods and the destination namespace, pod label and port.

The skeleton's `org-directory` ConfigMaps in the MCP and system namespaces include
`mcp-entitlements.json`: an object mapping team IDs to entitled server names. Services mount
the directory read-only and resolve users' current teams on each request. This component
does not create or duplicate the skeleton-owned directory ConfigMaps.

### Registry schema

The canonical examples are [registry.example.yaml](registry.example.yaml) and
[context-sources.example.yaml](context-sources.example.yaml). Both require `version: 1`.
The normalizer validates references before the plugin runs. Files use the organization's
restricted YAML subset: block collections, scalar flow lists/maps, no duplicate keys, aliases,
tags, tabs or multiline scalar values. The following is the reference normalizer's v1 schema;
descriptive fields are not stronger validation guarantees than the rules listed below.

| Server field | Meaning and validation |
|---|---|
| `name` | Unique registry server name; teams refer to it literally. Use Kubernetes-safe names. |
| `description` | Human-readable purpose. |
| `transport` | Exactly `stdio` or `http`. SSE-only transport is unsupported. |
| `command`, `args` | Stdio executable and argv. `command` required for stdio; `args` defaults to `[]`. No shell interpolation. |
| `image_layer` | Required for stdio; server must be baked into the relevant image. |
| `env` | Non-secret environment settings; defaults to `{}`. Names containing `KEY`, `TOKEN`, `SECRET`, `PASSWORD` or `CREDENTIAL` are rejected, case-insensitively. No credential values are permitted. |
| `auth` | `none` or `pod-identity` in v1; HTTP cannot use `none`. |
| `clients` | A subset of `claude`, `codex`, `kimi`, `hermes`, but any `kimi` entry is rejected in v1. |
| `allowed_teams` | Exact team IDs or `*`; a team's grant must be allowed here. No wildcard pattern matching. |
| `context_tags` | Descriptive tags; no permission effect. |
| `tool_timeout_s` | Tool timeout, default 120; rendered as Codex `tool_timeout_sec`. |
| `tools_readonly` | Descriptive read tool names, default `[]`; implementations must enforce their own authorization. |
| `deploy` | HTTP deployment: `{image, port, path}`. Use an immutable image digest. Exactly one of `deploy` and `service` is required for HTTP. |
| `service` | Existing HTTP service: `{namespace_ref, name, port, path}`. Namespace reference must exist in `org.namespaces` and cannot be `user_prefix`. |
| `server_egress` | Upstream `{host, port}` records; default `[]`. Host intent is documented but Kubernetes NetworkPolicy cannot enforce a DNS name. v1 public upstream access is TCP 443. |
| `server_secret` | Optional `{name, key}` credential reference. Deployed servers mount that key as `/etc/agent-array/upstream/credential`; sessions never receive it. |

Stdio entries require `command` and `image_layer` and cannot contain `deploy` or `service`.
Deployed HTTP URLs are `http://mcp-<name>.<NS_MCP>.svc:<port><path>`. Service URLs are
`http://<service.name>.<resolved namespace_ref>.svc:<port><path>`.

`none` is restricted to stdio. `pod-identity` authenticates the caller using the projected
`<project>-mcp` token and TokenReview. `per-user-token` is reserved for phase 2 and rejected
in v1: a token broker will exchange the pod token for per-user downstream tokens so downstream
ACLs can apply without putting upstream tokens into managed config. There is no v1 token broker.

### Context-source schema

| Source field | Meaning and validation |
|---|---|
| `name` | Unique source name, referenced by `teams.yaml` `context_sources`. |
| `kind` | Describes `code-snapshot`, `code-index`, `docs`, `tickets`, `wiki` or `chat`; the reference normalizer does not validate this enum. |
| `delivery` | Exactly `push`, `mcp` or `feed`. |
| `allowed_teams` | Team IDs or `*`; the plugin intersects this with team source grants. |
| `estates_from` | Push estate source, normally `estates`. |
| `ttl_hours` | `{default, max}`, normally 12 and 24; ingestion must enforce TTL limits. |
| `served_by` | Required for `mcp` and `feed`; must name a registry server. The plugin also requires that server's entitlement. |
| `builds_from` | Describes a source dependency; the reference normalizer does not validate this reference. |
| `languages` | Descriptive indexing language list. |
| `tags` | Descriptive context tags. |

The reference validates unique server/source names, server transport/auth/client rules,
stdio/HTTP shape, service namespace references, secret-looking environment names, team server
grants against `allowed_teams`, team source references and `served_by` references. It does not
validate unknown keys, source kinds, timeouts, ports, feed schedules or source dependency cycles.
These must be reviewed during registry changes; schema descriptions do not imply runtime validation.

### Team grants and adding a server

Add a reviewed registry entry, bake stdio code into its image or publish an authenticated
HTTP image, then add its exact name to a team's `mcp_servers` in `teams.yaml`. Entitlement is
the union of a user's teams' grants, intersected with the server's `allowed_teams`. Rendering
then intersects with each enabled tool's `clients`. Add source entries and team `context_sources`
grants separately. Register credential names, render, inspect the output and run the offline tests.

| Client | Managed configuration | Support |
|---|---|---|
| Claude | `/etc/claude-code/managed-mcp.json`; server loading controlled exclusively by this file; redundant allowlist belt; HTTP `headersHelper` calls `aa-mcp-token` | Managed path/helper shape confirmed by vendor evidence. Keep `allowManagedMcpServersOnly: true`. |
| Codex | `/etc/codex/requirements.toml` identities plus `/etc/codex/managed_config.toml`; HTTP routed through aa-mcp-bridge | Native identity/bearer fields confirmed; VERIFY bridge integration on pinned 0.160.0. |
| Kimi | No MCP ConfigMap | Disabled in v1; client entries refused. |
| Hermes | Image-baked arrayops stdio entry | Ops module only; never inserted into coding session configs. |

The session task owns the policy merge, managed-only locks, token helper, token projection,
hash verification and CLI restart behavior. Codex exports the projected token at startup;
sessions must restart or refresh it before token expiry. These are vendor integration gates,
not claims that the pinned binaries have been tested here.

## Configuration

This component reads `org.namespaces`, `org.project`, vendor enablement, team grants, users,
estates, registry and source records. Templates consume `PROJECT_NAME`, `LABEL_PREFIX`,
`USER_NS_PREFIX`, `NS_MCP`, `NS_SYSTEM`, `APISERVER_URL`, `APISERVER_ENDPOINT_IPS_JSON`,
`APISERVER_ENDPOINT_IP`, `APISERVER_SERVICE_IP`, `APISERVER_PORT`, `CLUSTER_DNS_IP`,
`PRIVATE_CIDRS_JSON`, and MCP entity keys. No component defaults are declared.

The `k8s/server/RENDER-IF` sentinel (`version == 0`) disables generic rendering of the
per-MCP subtree for valid v1 models. The plugin alone renders those templates, removing
the upstream Secret mount when unset, expanding all API endpoints and removing public egress
when no upstream is declared. This avoids duplicate manifests and invalid empty Secret names.

## Secrets

See [secrets.required.yaml](secrets.required.yaml). The `per-registry-entry` item is a descriptor,
not a Secret to create: each deployed entry's `server_secret.name` and `.key` are the actual
requirements. Stdio environments carry non-secret settings only; HTTP config carries credential
environment names or helper paths, never token values.
The plugin also emits `files/mcp/secrets-per-registry.json` with the actual deployed server,
namespace, Secret name and key requirements for provisioning review.

## Deploy

Render with `python tools/render/render.py --org org/org.yaml --out rendered --strict`.
Argo applies global MCP manifests, each user's manifests and each deployed server's manifests.
The organization directory must be projected into the MCP and system namespaces by the
directory owner. Build adapters from [the server template](servers/_template/README.md);
unmodified upstream third-party servers must be placed behind an adapter implementing this auth contract.

## Verify

Run `python -m unittest discover -s mcp/tests` and the two server suites under `servers/`.
Tests use the unchanged canonical fixture and run without cluster access. Platform admins
verify audience-bound TokenReview, CNI destination matching, directory mounts, CLI managed
configuration acceptance and same-user-only access before enabling servers.

## Rollback

Remove team grants, render and sync, then restart affected sessions. Remove a deployed server
only after its grants and source references are removed. Revocation is rechecked on each HTTP
request; ConfigMap projection propagation still has Kubernetes's normal delay.

## Security notes

All repository, ticket, wiki, alert and API text is data. Servers return explicit untrusted-data
wrappers; text that requests new credentials, policy changes or network access has no authority.
Snapshot ingestion must strip `.mcp.json`, `.claude/`, `.codex/`, `.kimi-code/` and `.agents/`,
and retain secret scanning and receiver-side estate/vendor/TTL checks.

NetworkPolicy cannot express `server_egress.host`. v1 permits public TCP 443 excluding configured
private CIDRs only when upstreams are declared; this is a coarse boundary, not a hostname allowlist.
Upgrade using the sessions' exact-host egress gateway pattern: deny direct public egress,
allow each server to its dedicated gateway, and enforce literal host/SNI allowlists there.
API endpoint and service-IP rules are both present because CNI enforcement may occur after DNAT.
Every HTTP call is audited without prompts, payloads, tokens or file contents. Authentication
failure and directory failure deny access. The farm's cross-namespace SA can read/scale session
resources; server-side ownership checks remain essential in addition to RBAC.

VERIFY: validate Codex stdio transport through `/usr/local/bin/aa-mcp-bridge` on the pinned CLI. The bridge reads the projected token for every HTTP request. Native `bearer_token_env_var` is confirmed by the Codex configuration reference, but reads the environment at process start; export before launch and restart after rotation when using native HTTP.

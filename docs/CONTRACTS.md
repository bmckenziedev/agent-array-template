# Cross-component contracts

## Names, ports and labels

Service names are stable routing interfaces. Port 8080 is named `http`; ServiceMonitors
reference the named Service port. LiteLLM isolates unauthenticated Prometheus metrics
on port 4001 (`metrics`), reachable only from monitoring. Model lane Service names are
the lane names verbatim. Session gateway clients carry `<label-prefix>/llm-client: "true"`.
Account metric labels never contain prompts, tokens or task IDs.

## Identity and groups

The v1 username claim is `sub`. Kubernetes subjects use OIDC_GROUP_* / TEAM_OIDC_GROUP;
application consumers use raw GROUP_* / TEAM_IDP_GROUP. Namespace derivation is
`user_ns_prefix + slug`, after matching the authenticated immutable subject to users.json.
Cloudflare Access panel identities match the verified email claim instead of its subject.
Chat identities use the named hermes-allowed-users Secret mapping chat ID to slug, then
directory team membership. Directory emails and subjects are accepted public organisation
metadata, available to registered team groups; they contain no credentials.

## Rendering

Defaults are deep-merged into components/modules before activation. Missing RENDER-IF
paths are false. Equality compares scalar string forms. Disabled plugin directories are
not imported. Duplicate output paths fail. Registry files resolve against --root.
Placeholder lint excludes tests, fixtures and docs; static checks cover supported config
extensions inside k8s/helm. Authored YAML uses the strict subset and exact uppercase keys.
Session images and component C_*_IMAGE values require nonzero reviewed digests in strict
mode. Generated manifests are regenerated, never hand-edited.

## GitOps coverage

Every Kubernetes manifest belongs to exactly one Application or generated ApplicationSet
source, with its kind and namespace permitted by the AppProject. Directory sources use
recurse: true and include: '*.yaml'. Kubernetes manifests do not belong under files/;
plain Prometheus rule groups do. Services permits only ClusterRole and ClusterRoleBinding
as cluster kinds. Teams and users have separate ApplicationSets. Namespace objects carry
Prune=false,Delete=false. Bootstrap is a manual runbook; Application waves are advisory.
StatefulSet Applications ignore replicas and use RespectIgnoreDifferences=true. Suspension
requires a zero-pod quota and an explicit scale to zero.

## Networking and admission

DNS uses the kube-system namespace selector together with k8s-app: kube-dns, UDP and TCP
53. Service-IP rules alone are insufficient after DNAT. API access includes endpoint
IPs and ports plus the portable Service-IP rule; keep endpoints synchronised with
kubectl get endpoints kubernetes. API proxy and webhook ingress includes API endpoints
and control-plane overlay addresses. Cilium additionally uses toEntities: kube-apiserver.
Default-deny does not grant connectivity; baseline allow sets cover control-plane
internals, monitoring scrapes and explicitly configured external routes.

CONNECT on exec, attach and portforward uses VAP, no CONNECT webhook. It checks the
namespace holder annotation or ticketed break-glass membership, with has() guards and
Deny/Audit actions. Storage lookup admission retains its TLS fail-closed webhook.
Positive and relevant negative server dry-runs precede Deny bindings. Root remains trusted.
Session egress excludes private ranges, configured node LAN addresses and 0.0.0.0/8,
224.0.0.0/4 and 240.0.0.0/4. Configure non-private LAN /32s explicitly.

## Pace and leases

The read-only proxy URL is `/api/v1/namespaces/<system>/services/pace:8080/proxy/...`;
RBAC resourceNames is exactly `pace:8080`. Proxy account listings have no caller identity;
the CLI filters the public response locally. No secrets belong in that response.
`aa-lease hold [--account A] -- <cmd...>` runs a child, renews every 60 seconds in the
foreground and releases on exit or signal. Seats remain holder-started; API automation
uses organisational API accounts. Per-account concurrency, spacing and caps apply.

## MCP authentication and managed policies

`aa-mcp-token` emits one JSON object of string Authorization headers. The Codex
`aa-mcp-bridge <url>` uses stdio JSON-RPC and streamable HTTP, rereading the projected token
on each request with bounded input/output and no credential logging. Native Codex
bearer_token_env_var is read at process start; export before launch and restart after
rotation. The bridge remains subject to pinned CLI deployment verification.

Claude managed-mcp.json presence provides exclusive server control and loads entitled
servers. allowedMcpServers does not restrict those managed servers in v2.1.259+;
serverName entries are a redundant belt, deniedMcpServers subtracts, and serverUrl is
preferred. Keep allowManagedMcpServersOnly:true and allowedMcpServers:[] for non-managed
paths. Do not pass --mcp-config/--strict-mcp-config with managed-mcp.json. managedMcpServers
never carries command or headersHelper; world-readable managed env contains no secrets.
API-key automation has a separate policy without forceLoginOrgUUID/forceLoginMethod.
Kimi MCP is disabled in v1; its wrapper-managed org lock is not a vendor managed config.

## Metrics

Pace owns aa_pace_account_info{account,vendor,type,holder,owner_team,team,usage_source} 1;
team is owner_team, or the holder primary team for seats. Usage/routing rules join on
account. LiteLLM spend reporting runs inside the gateway namespace with the master key
and sends bounded account usage reports to pace. otel usage is not implemented in v1.
Factory alone owns its rules: aa_factory_queue_ready{team,lane},
aa_factory_dispatch_total{team,lane,outcome}, and
aa_factory_oldest_ready_age_seconds{team,lane}.

## Optional forge identity and supervision

A user git block contains only a credential Secret name, provider and username. The
Secret key is token, provisioned/revoked for that user; the CLI-only read-only mount and
helper never rely on a credential left in a home. Commit email and author come from the
registry. Unsupported providers require an approved helper extension.

Enabled supervision selects gated StatefulSet variants and keeps separate PID namespaces,
UID 1000 for CLI/supervisor, read-only transcript subPaths and a sidecar-private control
socket. Only the request-only permission socket is shared. Allowed token audiences are
<project>-mcp, -pace, -supervisor and -supervisor-hook, with expiry <=3600 and supervisor
tokens confined to the appropriate container. No console credential Secret enters pods.
Only holders spawn/type/decide; audited break-glass may observe/interrupt/stop. Notifier
credentials belong to an external relay extension. The sidecar uses the exact configured
digest-pinned image and a per-user supervisor-policy
ConfigMap with a content hash. Console/hook egress uses exact configured CIDR/port pairs;
no inbound Service or metrics listener is emitted. Managed Claude configuration always
includes aa-permission, including holders without other MCP entitlements, with matching
allowlist entries and hashes. Strict rendering refuses all-zero C_*_IMAGE digests.

Holder RBAC already grants pods/exec create; no portforward grant is needed. The CONNECT
guard restricts exec/attach into supervisor to the holder, because local control is
holder-equivalent. Ticketed break-glass console actors may observe/interrupt/stop, never
spawn/type/decide. The aa sessions supervise wrapper selects the labelled holder pod and
executes aa-supervise in the sidecar with stdin preserved.

Supervised CLI env contains registry GIT_AUTHOR_* and GIT_COMMITTER_* identity; plain
variants receive no GIT_* additions. Optional named forge credentials remain CLI-only;
they do not grant the supervisor credential access. No console/notifier Secret is allowed.
Tier quotas reserve 50m/64Mi requests and 500m/256Mi limits per permitted pod when enabled.
Login provisioning pre-creates projects and sessions with uid/gid 1000 and mode 0700.
Kubelet subPath behavior still requires deployment verification.

The offline checker verifies image admission coverage, policy producers and hashes, MCP
permission configuration and hashes, private mounts, exact token audiences/expiry,
transcript subPaths, same uid/gid, separate PID namespaces and bounded RAM socket volumes.

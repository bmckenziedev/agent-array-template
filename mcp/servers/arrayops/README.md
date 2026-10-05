# Arrayops read-only MCP

Arrayops exposes bounded cluster observations to the Hermes ops chat module. Its stdio
interface keeps alert and metric labels inside an explicit untrusted-data wrapper and has
no machine power or write tools.

## Interface

`arrayops-mcp --read-only` exposes `cluster_status` (nodes' readiness and capacity),
`prom_query` (one instant query) and `alerts` (active alerts). Every Kubernetes request is
GET, including the Prometheus and Alertmanager API-server Service proxy paths. Standard
newline-delimited JSON-RPC supports initialize, list, call and ping. There is no HTTP listener.

## Configuration

The platform-owned, read-only `/etc/hermes/arrayops-readonly.conf` must contain
`ARRAYOPS_READONLY=1`, `APISERVER_URL`, `MONITORING_NAMESPACE`, `PROMETHEUS_SERVICE` and
`ALERTMANAGER_SERVICE` (service names including proxy port). The file is rechecked on each call.
Missing, disabled, non-root-owned or group/world-writable mode files refuse queries; environment
variables cannot override them. Mount it from a ConfigMap into the non-root Hermes container.
The module image copies this directory to `/opt/arrayops/` and marks `arrayops-mcp` executable.
The runtime is included here so that copy is self-contained.

## Secrets

Only the Hermes pod's scoped in-cluster SA token and CA are read. The ops module must supply
RBAC for nodes get/list and the named monitoring services/proxy GET; no admin kubeconfig,
SSH credential or model key is used by arrayops.

## Deploy

The optional Hermes ops chat module owns the image, mode ConfigMap, service account and
monitoring egress. Register `arrayops` with `clients: [hermes]`, `transport: stdio` and
`auth: none`; grant it only to the ops team. It is never offered to coding sessions.

## Verify

Run `python -m unittest discover -s mcp/servers/arrayops/tests`. Tests exercise GET-only
dispatch, argument checks, untrusted wrapping, protocol negotiation and fail-closed file mode.
Platform admins confirm the mounted file is root-owned and not writable by the agent.
The executable exits 0 at EOF; argparse errors exit 2; unavailable mode fails startup.

## Rollback

Disable the Hermes module or remove its arrayops toolset grant and roll back the module image.
Do not broaden RBAC or change the mode file to restore a denied query.

## Security notes

The source's fixed-argv/read-only-file/untrusted-data principles are retained; wake/sleep
scripts and rig enums are removed. No shell, subprocess, local discovery or agent-writable
configuration participates in requests. PromQL is encoded as a single query parameter and
limited to 2,000 characters. Output and upstream response bounds follow the shared runtime.

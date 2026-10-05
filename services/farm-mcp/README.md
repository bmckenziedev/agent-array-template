# Farm MCP service

Farm provides authenticated cluster status, bounded model calls, factory submissions and
own-session scaling. Team account routing and pace leases replace desktop authority and
process-local concurrency counters.

## Interface

ClusterIP `farm-mcp` in the system namespace listens on port 8080, POST `/mcp` for stateless
streamable HTTP MCP and GET `/healthz` and `/metrics`. It uses the
[shared HTTP/auth runtime](../../mcp/servers/_template/README.md). HTTP requests carry the
session's projected `<project>-mcp` token. TokenReview, namespace labels, active directory
membership and farm entitlement are checked on every request; denial is HTTP 403.

| Tool | Behavior |
|---|---|
| `farm_status` | Configured lane summary, active leases for visible accounts, team factory batches from `GET /v1/batches?team=`, sanitized pace accounts and own pods. Disabled factory is explicit in the queue summary. |
| `farm_sessions` | Lists only caller-labelled pods in the caller's namespace; returns a small status projection. |
| `farm_llm` | Routes a `prompt`, `model` and `data_class` through pace for the chosen member `team` and optional `task_class`; makes exactly one completion, with optional `max_tokens` capped at 4096. |
| `farm_batch` | Up to 16 validated task objects using the `farm_llm` arguments. Calls pace `POST /v1/route` for each; executes serially and returns per-task success/error. No seat or local mode exists. |
| `farm_units` | Submits `estate_id`, object `cards`, optional `team` and clamped `priority` to factory `POST /v1/batches` with `template: doc_map` and the caller's token. Factory approval is unchanged. Disabled factory produces a clear error. |
| `farm_scale` | Takes an own `statefulset` and integer `replicas`; verifies user/tool/account labels and seat ownership. Clamps to the minimum of effective tier max replicas and account max concurrent sessions; zero is supported. |

Pace routes are independently filtered against the directory: account type API/pool, team pool
membership, ownership/sharing, allowed vendor for the data class, team model allowlist and
account model allowlist. A lease is acquired before minting. One call uses a 120-second LiteLLM
key with `team_id`, `user_id`, exact model, task budget, alias and task/account/data-class/client
metadata; key deletion and lease release run on success and exceptions. Each upstream request
has a 60-second timeout. Calls never log prompts, payloads, bearer tokens or keys. Metric names
are `aa_mcp_requests_total`, `aa_mcp_auth_denied_total` and `aa_mcp_request_seconds`.

## Configuration

`render_plugin.py` (`PLUGIN_NAME = "farm-mcp"`) emits `farm-mcp-config`: label prefix, session
tiers, estates, factory module enablement and optional GPU lane descriptors. No new global
placeholder or component-default key is declared. The effective tier is a user's explicit tier
or primary team's tier, matching the normalizer. The module lane list is descriptive; factory
remains authoritative for scheduling.

Templates use `IMAGE_FARM_MCP`, `PROJECT_NAME`, `LABEL_PREFIX`, `USER_NS_PREFIX`, `NS_SYSTEM`,
`NS_LLM`, `NS_FACTORY`, `APISERVER_URL`, `APISERVER_ENDPOINT_IP`, `APISERVER_ENDPOINT_IPS_JSON`,
`APISERVER_SERVICE_IP`, `APISERVER_PORT`, `CLUSTER_DNS_IP` and per-user `USER_NS`.
Service URLs become `PACE_URL`, `LITELLM_URL`, `FACTORY_URL`. The org-directory ConfigMap,
including team entitlements, mounts read-only; pace identity uses a separate projected `<project>-pace` SA token
and rereads it for each request. TokenReview and namespace GET use the service's ordinary
in-cluster API token. The scale RoleBinding is generated per non-offboarded user namespace.

## Secrets

See [secrets.required.yaml](secrets.required.yaml). Secret `litellm-mint`, key `LITELLM_MINT_KEY`, is mounted
by name in the system namespace. It permits only `/key/generate`, `/key/delete`, `/key/info`.
No LiteLLM master key is read. Provider credentials stay in the LLM namespace. Call keys are
short-lived and removed after use; cleanup failure returns a sanitized error and expiry remains
the backstop. There is no configurable credential string in the registry or ConfigMaps.

## Deploy

Build from repository root with a mandatory approved Python image digest:

```sh
docker build --build-arg PYTHON_IMAGE=python:3.12-slim@sha256:<approved-digest> -f services/farm-mcp/Dockerfile .
```

Publish the digest as the org `farm-mcp` image, provision the scoped mint Secret, render with
`tools/render/render.py --strict` and sync global and user manifests through Argo. The directory
owner projects `org-directory`, including `mcp-entitlements.json`, into the system namespace.
All network access is limited to DNS, API server, pace, LiteLLM and
factory. LiteLLM recognizes the pod's `llm-client: "true"` org label. Farm uses UID 10001,
read-only root filesystem, dropped capabilities and RuntimeDefault seccomp.

## Verify

Run `python -m unittest discover -s services/farm-mcp/tests`. Tests stub TokenReview, pace,
LiteLLM, factory and Kubernetes and run a loopback port-0 HTTP server that is stopped and joined.
Platform admins verify TokenReview and namespace permissions, directory mounts, the scoped mint
key's allowed routes, model fallback behavior, pace platform-SA lease authorization and CNI
service/endpoint policy matching before release. The executable exits 0 on normal completion;
argparse errors exit 2.

## Rollback

Remove farm team grants, render/sync and restart affected sessions. Roll back the image digest
and remove per-user farm RoleBindings if withdrawing the service. Existing call keys expire
after 120 seconds and pace leases after 300 seconds; failed cleanup must be investigated without
printing their values.

## Security notes

Dropped tools: `farm_claude`, `farm_codex`, `farm_kimi` and `farm_auto` scripted seat dispatch
are excluded because seats are interactive only. `farm_send`, `farm_get`, local paths,
desktop CLI discovery, personal concurrency settings, node enums and rig wake/sleep are excluded
because cluster tools must not inherit a desktop's filesystem or administrator authority.

Scale uses the caller's namespace and Kubernetes resourceVersion, so a competing update fails.
The farm SA's per-user Roles grant pods get/list, StatefulSets get and scale get/update/patch;
no exec, secrets, pod creation or arbitrary resources are allowed. RBAC cannot express ownership
and tier ceilings inside scale payloads, so those checks stay in the authenticated service.
All returned text is untrusted; upstream error bodies never reach logs or clients.

Pace is the sole owner of leases. The current section-7 lease shape (`kind: session`, synthetic
`pod: farm-<task-id>`) is reused for API/pool calls; pace must authorize this platform SA and
count these leases consistently with its account ceilings. Farm never falls back to in-memory
counters or seats when pace is unavailable.

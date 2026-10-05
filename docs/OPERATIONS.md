# Operations

Treat readiness, authenticated function, isolation and observability as separate checks.
Platform admins maintain desired state through reviewed registries and rendered GitOps;
routine users attach only to own sessions through [the CLI](../tools/aa/README.md).

## Operating loop

Inspect runtime/node health, replica placement, failed policy merges, account headroom and
usage age, budgets, MCP denials/latency and backup age. Missing telemetry is unknown,
not zero usage or healthy service. Export ephemeral work and stop tasks before restart,
drain or scale-down. Homes survive only on their configured nodes and are never backed up.

After rollout verify real holder login and one bounded approved task. Running pods do not
prove authenticated vendor calls or safe context delivery. Compare firing alerts to the
saved baseline; rollback unexpected failures without relaxing admission or isolation.

## Runbooks

| Operation | Procedure |
|---|---|
| Add person | [Onboard user](runbooks/onboard-user.md) |
| Revoke access/home | [Offboard user](runbooks/offboard-user.md) |
| Add entitlement boundary | [Add team](runbooks/add-team.md) |
| Add seat/API/local pool | [Add account](runbooks/add-account.md) |
| Integrate tool | [Add MCP server](runbooks/add-mcp-server.md) |
| Exceptional access | [Break-glass](runbooks/break-glass.md) |
| Renew credentials | [Rotate secrets](runbooks/rotate-secrets.md) |
| Change cluster/chart/CLI | [Upgrade](runbooks/upgrade.md) |
| Drain/restart/encryption | [Maintenance window](runbooks/maintenance-window.md) |
| Prove recovery | [Restore drill](runbooks/restore-drill.md) |
| Contain/investigate | [Incident first response](runbooks/incident-first-response.md) |
| Understand paging groups | [Alert catalog](runbooks/alert-catalog.md) |

## Observability and paging

[Monitoring](../monitoring/README.md) is authoritative for rendered rule names, thresholds
and dashboards. Metric prefix is `aa_`; keep task IDs, user subjects and prompts out of
labels. Pace reports window usage/caps, leases, denials, routes and reading age. MCP reports
requests/auth denials/latency. Factory separates eligible ready demand, inflight slots,
retries/bounces and quality. Do not infer suspended lanes from NotReady or fill absent
series with zero.

Critical alerts reach on-call; warnings follow configured quiet-hour/digest policy; unknown
severity has a non-null fallback. Notifier URLs are Secrets. Test actual receiver and
independent heartbeat delivery. Scope maintenance silences narrowly; missing labels must
not cause broad inhibition of unrelated alerts.

Ship service stdout audit and [API metadata audit](../ops/audit/README.md) to protected
retention. Never collect Secret/ConfigMap bodies, prompts or payloads. Review break-glass
and failed authorisation independently of metrics.

## Capacity and queues

Scale within account leases, tenant quota and measured per-node request headroom including
runtime overhead. Replicas add capacity, never allowance. Reconcile stale usage rather
than inventing readings. Every task keeps bounded gateway models/budget/timeout.

For [factory](../modules/factory/README.md), inspect approved revisions, dependencies,
fair share, inflight limits and deterministic failures before retries. Pause corrupted
context or quality regressions and preserve result metadata. Cancel unauthorised batches;
do not produce filler after approved input exhaustion or retry beyond card policy.
Factory never starts seat turns.

## Symptom triage

| Symptom | First discriminator |
|---|---|
| Pod pending | Home-node/PV affinity, runtime label, quota and allocatable requests |
| Entrypoint refusal | Managed-file hash, policy/env; never bypass preflight |
| Login failure | Correct holder/org workspace, contract and home node; never copy auth |
| MCP 403 | Token audience/expiry, namespace labels and entitlement |
| Snapshot rejected | Estate/target/class, manifest/TTL/deny globs, context-policy presence |
| Route denied | Account/team/class/vendor/model access, key budget and usage age |
| API service fails under default-deny | Endpoint AND service-IP rules and actual CNI DNAT behavior |
| Alert absent | Scrape validity, rule selection, routing Secret and end-to-end delivery |

Component READMEs supply exact commands/rollback. Save non-secret status only; sensitive
Helm output and authenticated response bodies are not troubleshooting evidence.

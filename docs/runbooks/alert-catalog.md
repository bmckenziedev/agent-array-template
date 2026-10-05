# Alert catalog

This short outline groups the exported rule families derived from source rule groups.
[Monitoring](../../monitoring/README.md) is authoritative for current rendered alert names,
thresholds, optional-module gating and tests; regenerate this outline when families change.

| Family | Signal | First response |
|---|---|---|
| Node/control plane | Readiness, disk/memory, datastore/API health | Check failure domain and recovery readiness |
| Network | Reachability and traffic anomalies | Inspect permitted routes and host/upstream rules |
| Sessions/services | Workload availability, policy and service failures | Check managed hashes, auth and actual task health |
| Gateway/tenant | Model service, budget/usage and quota pressure | Confirm account attribution and usage freshness |
| Factory | Demand, slots, retry/bounce/quality and stale telemetry | Inspect approved inputs and deterministic checks |
| GPU lanes | Serving/accelerator metrics and missing scrapes | Distinguish unknown telemetry from idle capacity |
| CI | Runner/controller health and trust isolation | Pause dispatch and inspect scoped runner access |
| Hostwatch/Kata cleanup | Expiry, host/runtime cleanup and isolation drift | Stop risky placement until runtime is proved |
| Sealing/GitOps/ops | Controller health, drift and operational failures | Review manual sync and required-secret scope |
| Backup coverage/drill | Stale backup, missing keys and failed recovery | Stop destructive maintenance and prove restore |

Route critical signals to on-call, warning digests through configured quiet hours, and
unknown severity through a non-null fallback. Test receiver plus independent heartbeat.
Use scoped expiring silences and avoid missing-label inhibition of unrelated workloads.

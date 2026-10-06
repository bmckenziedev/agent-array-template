# 0019: Pace as a lease service

Status: Accepted (design decision D9).

## Context

The source tracked usage with process-local counters and file scraping, which cannot coordinate several users or pods. More pods must not create more vendor allowance (R4).

## Decision

Pace is a stdlib Python service with SQLite on a PVC and exactly one replica. Seat leases enforce each account's concurrency and start spacing. For interactive use it fails open when unreachable, unless `sessions.lease_fail_closed` is set.

## Consequences

Account limits are enforced across all pods, and state survives restarts. One replica is a deliberate availability limit: the vendor still enforces the real limits, so an outage does not block interactive work by default. Organisations that prefer refusal set `lease_fail_closed: true`.

Integration refinement: the deployment uses Recreate so SQLite admission stays globally serialised. Server leases last 300 seconds; `aa-lease hold` runs the child in the foreground, renews every 60 seconds and releases on exit or signal. Users read accounts only through the API-server proxy, with RBAC `resourceNames` exactly `pace:8080`. API/pool writers must be listed in `components.pace.platform_writers` (empty by default). Pace owns `aa_pace_account_info`, and LiteLLM sends bounded account-scoped spend reports. Seat admission permits unreported windows while collectors start; API/pool accounts with no current readings are excluded from routes. Codex keeps its local auth lock so operations stay serialised when pace is unreachable.

## Alternatives considered

- Process-local counters: rejected because replicas multiply allowance.
- A replicated database: rejected for v1 because it adds operational weight for a low-rate service.
- Fail closed by default: rejected because a pace outage would block all interactive work that the vendor would still permit.

## What would make us revisit it

Availability requirements that one replica cannot meet, vendor usage APIs that make leases redundant, or a policy decision to fail closed by default.

## Related files

- [services/pace/README.md](../../services/pace/README.md)
- [services/pace/pace.py](../../services/pace/pace.py)
- [sessions/common/bin/aa-lease](../../sessions/common/bin/aa-lease)
- [org/org.example.yaml](../../org/org.example.yaml)
- [docs/CONTRACTS.md](../CONTRACTS.md)

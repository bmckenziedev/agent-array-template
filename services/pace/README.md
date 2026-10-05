# Pace service

Pace coordinates account usage and session leases across users. Subscription seats stay interactive; automation routes only to team API accounts and pools. State survives service restarts on a SQLite PVC.

## Interface

The ClusterIP Service `pace` listens on port 8080. The HTTP implementation uses Python 3.10 or later and the standard library. JSON endpoints follow the platform contract:

| Method and path | Input or result |
| --- | --- |
| `GET /healthz` | `{"status":"ok"}` |
| `GET /metrics` | Prometheus account windows, usage ages, active leases, denial and route counters |
| `GET /v1/accounts` | Public account metadata and window readings; never credentials or secret references |
| `POST /v1/usage` | `{account_id, window, used_pct, resets_at, source}`; 204 |
| `POST /v1/lease` | `{account_id, kind:"session", pod}`; `{lease_id, expires_at}` |
| `POST /v1/lease/<id>/renew` | Renew the caller's lease for 300 seconds, rechecking caps |
| `DELETE /v1/lease/<id>` | Release the caller's lease; 204 |
| `POST /v1/route` | `{team, task_class, data_class}`; eligible accounts ranked by headroom |

Mutations and routing require a projected bearer token reviewed with audience `<project>-pace`. Invalid authentication returns 401, forbidden identity or account access 403, unknown resources 404, and invalid JSON or fields 400. Lease refusal returns 409 with `max_concurrent`, `spacing`, or `cap`. Expired leases cannot be renewed. Active leases are counted transactionally under the service lock. The deployment has one replica and Recreate strategy, so SQLite admission remains globally serialized.

Users read accounts through the API-server service proxy. Kubernetes authorizes the OIDC user with a per-team Role granting only `get` on the `org-directory` ConfigMap and the `pace` service proxy. The proxy does not forward a caller bearer token, so this read-only service endpoint has no bearer requirement. NetworkPolicy admits the API-server endpoints. The CLI selects the caller's seats and team pools for presentation. Cluster-internal session namespaces can also see this nonsecret account metadata.

## Configuration

`org-directory` supplies `accounts.json` (the account list), `users.json`, and `teams.json` at `/etc/agent-array/org/`. The render plugin emits a separate `pace-config` ConfigMap containing `pace.json`, projected into that directory. It contains `accounts.default_policy`, the `accounts.plans` catalog including any organizational calibration data, the identity audience and label prefixes, and the writer allowlist. No personal measurement seeds or machine-local collectors are included.

Component defaults are declared in [org.component.defaults.yaml](org.component.defaults.yaml):

| `org.yaml` setting | Default | Purpose |
| --- | --- | --- |
| `components.pace.platform_writers` | `[]` | Exact `namespace/serviceaccount` identities allowed to write or lease API/pool accounts and request team routes |
| `components.pace.storage` | `1Gi` | SQLite PVC capacity, rendered as `C_PACE_STORAGE` |

Platform admins must list farm-mcp and factory writer service accounts explicitly; the empty default denies their mutations. Seat writes and leases require the active holder's namespace SA `session`. Namespace ownership is verified from the `<label prefix>/kind=user-sessions` and `/user` labels. The namespace must begin with the configured user namespace prefix. Positive TokenReviews and their namespace identity are cached in memory for at most 60 seconds. Tokens are keyed by a SHA-256 digest, never logged, and no negative results are cached.

Each account can override cap, reserve, and minimum start spacing. Admission headroom is the minimum over windows of `min(cap_pct, 100-reserve_pct)-used_pct`. API/pool routes also filter team ownership/sharing, allowed data class and vendors, caps, daily caps, concurrency, and minimum start spacing. Ties sort by account ID. `task_class` labels the routing metric; no task calibration is fabricated.

Window names `5h`, `weekly`, `monthly`, `daily`, and `rolling` are supported. A window can also be `{name: rolling, duration_s: 604800}` or `{name: weekly, kind: rolling, hours: 168}`. Percentages are absolute readings, not additive input. Rolling windows persist positive changes between successive cumulative source readings as timestamped spend and expire each delta independently. A decrease in a source reading conservatively counts the new reading as spend after a source-cycle reset. The first reading counts all known consumption at its observation time because prior timestamps are unknown. Reported RFC3339 `resets_at` is authoritative for calendar windows; rolling expiry is derived from observed delta timestamps. Expired calendar windows reset usage to zero. Nonlocal API/pool accounts with unreported or expired observations are excluded from routes and leases until a collector reports again. Windowless local pools remain available. Seat admission permits unreported/reset windows while the holder relies on vendor limits and collectors initialize; observed caps still apply. Unreported calendar windows default to local Monday or month boundaries. Duration windows default to five hours or one day. Calendar boundaries use `zoneinfo` and UTC timestamps, including DST transitions. Runtime images provide the IANA database; Windows test environments use the pinned test-only `tzdata` package.

`daily_cap_pct_of_week` tracks positive weekly reading increases in the account's local date. A new weekly reset counts from zero. The initial reading conservatively counts the known weekly usage toward the day because earlier consumption cannot be reconstructed. Counter decreases within the same window do not erase daily spend. A new local date gets a fresh daily cap.

Configuration is loaded at process start. Roll the deployment after directory or writer-policy changes. Offboarding changes take effect after restart; cached namespace identity additionally has a 60-second lifetime. Database schema version 2 migrates an empty or version 1 database, preserving observed rolling spend; a future unsupported schema fails startup. WAL and a busy timeout are enabled.

## Secrets

No vendor credentials are consumed. Pace uses its own projected Kubernetes API credential for TokenReview and namespace lookup. Session callers use audience-scoped projected tokens. The account directory contains secret names only; the response view omits these references.

## Deploy

Render the organization configuration, then synchronize `rendered/global/services/pace` through Argo CD. Build with `docker build -f services/pace/Dockerfile .` and configure the resulting digest-pinned `IMAGE_PACE`. The base Python image is pinned to a verified registry digest. Apply directory, settings, PVC and RBAC resources before starting the deployment. The PVC is mounted writable; the container root filesystem is read-only.

The deployment consumes `IMAGE_PACE`, `NS_SYSTEM`, `PROJECT_NAME`, `STORAGE_CLASS_DEFAULT`, and `C_PACE_STORAGE`. Templates and the render plugin consume `LABEL_PREFIX`, `USER_NS_PREFIX`, `NS_FACTORY`, `NS_MONITORING`, API-server endpoint/service addresses and ports, and `CLUSTER_DNS_IP`. Per-team readers consume `TEAM_ID` and `TEAM_OIDC_GROUP`.

## Verify

Run offline from the repository root:

```text
python -m unittest discover -s services/pace/tests -t services/pace -v
```

Test dependencies are pinned in [requirements-test.txt](requirements-test.txt). Tests start the service and a fake TokenReview endpoint in-process on loopback with ephemeral ports and close all threads and servers. They cover all endpoints, authorization, thread races, renewal/release/expiry, spacing, caps, calendar and duration resets, DST, routing, metrics, persistence and template rendering. Kubeconform runs when installed and otherwise reports a skip.

Platform-admin live checks: confirm the configured audience is returned by TokenReview, validate namespace labels, confirm proxy traffic's source address is admitted by the CNI, exercise a holder and a different user's negative case, and verify collectors report authoritative resets for each vendor plan. Confirm Prometheus scrape permission and the installed storage class. No live checks are performed by the offline suite.

## Rollback

Retain the PVC and switch to the previous application image and rendered settings. Back up SQLite with its backup API or after stopping the deployment; copying a live main database without its WAL loses transactions. Do not start an older image against a newer schema without restoring its matching backup. Never run two deployments against this PVC.

## Security notes

Seat accounts never appear in automation routes. Every seat usage report and lease is authorized again by the service. API and pool writers must be explicitly allowlisted. A lease is bound to the caller service account; a different writer cannot release or renew it. Admission checks limit starts, not consumption within a running vendor turn; usage collectors must keep reporting while work proceeds.

Audit events are JSON lines with actor, path, outcome and refusal reason. They omit tokens, prompts, file contents and request payloads. Health and read-only metrics require no bearer token and are confined by ingress policy. Graceful SIGTERM/SIGINT shutdown stops accepting requests, waits for request threads, and closes SQLite. Directory changes require a rollout to avoid retaining obsolete account entitlement data.

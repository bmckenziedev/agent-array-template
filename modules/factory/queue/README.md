# Factory queue

The durable team queue stores bounded submissions pending team-lead approval and
records decisions in an actor-attributed SQLite ledger. The HTTP API and foreground
engine share this store; TokenReview establishes caller identity.

## Interface

`factory_queue.api_store.BatchStore(path, estates, teams, caps, snapshot_root)`
provides submit/get/list/approve/cancel/claim/complete. Identity maps contain user,
sub, teams and primary_team and are supplied only by authenticated service code.
`factory_queue.worker.run_foreground` is the pod's supervised engine loop.

The workstation-lane module retains a privileged transfer interface:
`python remote.py pc-pull` emits `{batch, config, cards, files}` with base64 file
bytes for one explicitly lead-approved snapshot batch. `pc-return` reads bounded
JSON `{batch, units: [{unit_id, status, output}]}` on stdin; each returned output is
independently gated by the factory. A workstation lease is never automatically
replayed. Commands run through an authenticated operator tunnel and expose no
listener. FACTORY_WORK selects the transfer state; the compatibility snapshot
queue lives at FACTORY_WORK/queue. No PC feeder or snapshot ingest command ships.
Production team HTTP batches use the shared BatchStore and do not bypass its approvals.

## Configuration

Queue caps default to 32 retained batches, 100 cards per batch and 1 MiB payload.
Terminal payloads and snapshots expire after the configured retention period;
audited decision events remain in the durable ledger. An explicit bounded immutable
snapshot mount is required at approval. Estates are
filtered by the submitting team from org-directory estates.json. Engine runtime
per batch is capped at 900 seconds. Claims are fenced by unique identifiers and
recovered only after expiration. Weighted per-lane dispatch operates across batches.

## Secrets

The queue stores no authentication token. Model secrets are supplied by the engine's
trusted lane configuration; see ../secrets.required.yaml.

## Deploy

The module API starts the worker in-process and joins it during shutdown. Mount
estate snapshots read-only. Approval copies selected bounded files into PVC state;
workers verify the approved manifest before packing. The compatibility queue is
for the separately deployed workstation adapter, whose operator must enforce team
lead identity before enqueueing a privileged transfer batch.

## Verify

`python -m unittest discover -s modules/factory/queue/tests -t modules/factory/queue`
proves bounds, lead decisions, cancellation, stale-claim fencing and snapshot safety.

## Rollback

Stop the foreground service, retain queue state and restore an earlier compatible
image. Pending approval never becomes ready merely through restart.

## Security notes

Membership cannot grant another team's estate access. Approval records actor subject
or slug and actor_kind=lead. Reserved fields cannot come from HTTP payloads.
Snapshots have bounded file counts/bytes, no links and a SHA-256 manifest. Worker
completion cannot overwrite cancellation or a newer lease.

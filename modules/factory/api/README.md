# Factory API

The standard-library HTTP service authenticates callers through Kubernetes TokenReview and
maps identities through the mounted organisation directory. It hosts the optional factory's
team controls inside the factory pod.

## Interface

Port 8080 exposes `POST /v1/batches`, `POST /v1/batches/<id>/approve`,
`POST /v1/batches/<id>/cancel`, `GET /v1/batches/<id>` and `GET /v1/batches?team=`.
Submission takes `{estate_id, template, cards, priority}` and an optional member team;
identity and data classification come from trusted directory records. Approval requires a
team lead. Cancellation requires the submitter or a team lead. Status is visible only to
members of an entitled team. Lists contain at most 100 summaries; results are capped at
512 KiB and audit history at 500 events. `GET /healthz` and `/metrics` are unauthenticated
and must be isolated by NetworkPolicy.

## Configuration

`PROJECT_NAME`, `USER_NS_PREFIX`, `LABEL_PREFIX`, `OIDC_USERNAME_PREFIX` and `APISERVER_URL`
configure identity validation. `FACTORY_STATE_DIR` defaults to `/work`; the directory
mount defaults to `/etc/agent-array/org`. Command options override listen address, port,
directory and SQLite state path. The directory provides `users.json`, `teams.json`,
`estates.json` and `mcp-entitlements.json`; policies are reread for each authenticated call.

`FACTORY_MAX_BATCHES`, `FACTORY_MAX_CARDS`, `FACTORY_MAX_PAYLOAD_BYTES`,
`FACTORY_STARVATION_N`, `FACTORY_WORKER_POLL_SECONDS` and `FACTORY_RETENTION_SECONDS`
consume module defaults. `--lanes` defaults to `/etc/factory/lanes.json` and
`FACTORY_SNAPSHOT_ROOT` points at administrator-provisioned immutable estate snapshots.
The worker freezes the approved bytes into PVC state, validates and expands cards,
and assembles integration-checked bundles before recording results.

Metrics refresh independently of scrapes and retain an idempotent ledger after task
retention. Queue gauges come from coherent SQLite read transactions. Counters record
one outcome per completed attempt and one final accepted or bounced lifecycle.
Generation occupancy intervals are unioned across slots for each lane/node. The
configured node mapping must match physical placement. Unknown node power state is
omitted; an external trusted power adapter is required before power-aware alerts
can fire. Generating a bundle does not emit an `applied` acknowledgement.

## Secrets

TokenReview uses the factory pod's projected API-server service-account token and CA.
Caller tokens are never stored or logged. Session MCP clients use the separate
`/var/run/agent-array/mcp-token/token` token with audience `<project>-mcp`.

## Deploy

The module Deployment runs `python -m factory_api.service` as its supervised foreground
process. Kubernetes supplies shutdown signals; bounded request handling and joined worker
threads keep service lifetime under pod supervision.

## Verify

Run `python -m unittest discover -s modules/factory/api/tests -t modules/factory/api`.
Tests use an in-process loopback TokenReview server and close every server on teardown.
Platform admins verify API-server TokenReview support for the configured pod audience and
OIDC user tokens before enabling portal submission. OIDC JWTs are never decoded and trusted
locally: the API server must authenticate them and return the requested audience.
Portal OIDC tokens can use a second TokenReview with the API server's configured
default audience, but that result is accepted only for a mapped OIDC subject.
Service accounts always require the explicit projected MCP audience; they cannot
use the OIDC review path.

## Rollback

Disable module sync and scale the factory Deployment to zero. Retain the PVC and audit
ledger; restore compatible image and directory configuration before resuming.

## Security notes

Only `system:serviceaccount:<user namespace>:session` identities in labelled user-session
namespaces or authenticated configured OIDC subjects are accepted. Suspended users,
unentitled teams and mismatched audiences are denied. Namespace labels are checked through
the API server. Submitted identities, data classes, lane URLs and account credentials are
not accepted. Audit output contains identifiers and decisions, never prompts or repo text.

# Module: factory (optional)

Factory expands reviewed templates and cards into small, gated work units. Teams share local lanes through weighted fair scheduling and approve immutable batches through an authenticated API. The estate index and stdio MCP server expose bounded discovery and submission interfaces.

## Interface

`factory-api` listens on port 8080 and exposes batches, approvals, cancellation, status and `/metrics`. Sessions call it using projected MCP tokens. The [engine](engine/README.md), [queue](queue/README.md), [index](index/README.md), [MCP server](mcp/README.md) and [benchmark](bench/README.md) describe their interfaces. Workstation pull/return commands remain available through `queue/remote.py` for the optional workstation-lane module.

## Configuration

Set `modules.factory.enabled: true`. Defaults are declared in `org.component.defaults.yaml`; teams supply weights, priority ceilings and per-lane limits through org-directory. Estates are allowlisted per submitting team. Lane endpoints use `lanes.v1` configuration and Services such as `lane-gpu-a` and `lane-gpu-b` in the models namespace.

Module defaults are `queue_max_batches: 32`, `queue_max_cards: 100`, `queue_max_payload_bytes: 1048576`, `queue_retention_seconds: 43200`, `starvation_dispatches: 20`, `lane_concurrency: 1`, `worker_poll_seconds: 1` and `storage_gib: 10`. Rendered `M_FACTORY_*` keys configure queue bounds, retention, fair-share starvation, lane slots, polling and PVC capacity. Lane node names must identify the actual configured model nodes before production metric collection.

## Secrets

The local lane token is referenced by `model-server-auth`; see [secret requirements](secrets.required.yaml). Kubernetes projects the factory service-account token for TokenReview and org-directory reads. Client MCP tokens are never logged.

## Deploy

CI builds the root-context [Dockerfile](Dockerfile). Render the enabled module templates and reconcile them through the organisation's GitOps workflow. Platform hardening creates the factory namespace. The engine and API run as supervised foreground processes within the pod; agents and operators never spawn detached processes.

The configuration producer must publish an `org-directory` ConfigMap into the factory namespace with `users.json`, `teams.json`, `estates.json` and entitlement data. Kubernetes cannot mount a ConfigMap from another namespace. Populate authorised immutable estate snapshots on the factory PVC before approving units that reference them.

## Verify

Run component test suites, template tests and the benchmark selftest. Platform admins verify TokenReview audience handling, lane authentication, network isolation, persistent recovery and metrics after deployment. Never promote a lane without a bench run on the organisation's own code.

## Rollback

Suspend submissions, drain or cancel approved work, preserve the PVC and restore the previous reviewed image and configuration. Preserve audit and lifecycle event records across rollback.

## Security notes

Identity comes from authenticated tokens. Only a team lead can approve that team's batches. Restricted data stays on local lanes; escalation uses pace API/pool routes. Repository text is untrusted data. Generated tests remain flagged NO-GO until their benchmark promotion gate passes.

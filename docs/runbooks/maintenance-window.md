# Maintenance window

Platform admin records window ID, scope, approvals, expected downtime and abort/rollback
criteria. Single-control-plane restarts interrupt API/scheduling; treat the platform as
unavailable even if worker pods continue. See [optional maintenance kit](../../modules/k3s-maintenance/README.md).

## Ordered procedure

1. Export all ephemeral work, stop or finish API/CI/factory tasks and prevent new dispatch.
   Record non-secret readiness/alert baseline and saved objects/Helm revisions.
2. Run read-only preflight: storage integrity/free space, locks, node/runtime/API health,
   no active risky jobs, no incomplete encryption rotation and functioning recovery access.
   Any unexplained failure is NO-GO; no generic acknowledgement bypass.
3. Take consistent datastore/application backups and verify encrypted off-cluster copy,
   sealing/encryption key coverage and a [restore drill](restore-drill.md). Login homes
   remain excluded. Backup or drill failure aborts the window.
4. For at-rest encryption changes, use the distribution's resumable staged procedure and
   verify completed re-encryption with a synthetic canary without printing data. Never
   remove encryption configuration while encrypted records still depend on it.
5. For audit changes, verify API returns and metadata records appear without Secret or
   ConfigMap bodies. Roll back exact configuration if API health fails.
6. Drain/restart workers sequentially, then control-plane members in the supported quorum
   sequence. Verify each returns before proceeding. Protected login volumes do not move;
   fresh login is required for a changed home node.
7. Remove obsolete datastore copies only after recovery proof and explicit path review;
   never perform blanket filesystem cleanup. Firewall changes remain additive.
8. Restore dispatch, check user/gateway/MCP boundaries and compare alerts; remove scoped
   silences and confirm heartbeat/on-call delivery.

## Gates and rollback

Helpers default to dry-run and require `--i-am-in-the-window` for maintenance mutations
plus any documented apply flag. No detached processes. Stop on NO-GO or unexpected
API, runtime, admission, storage or alert failure.

Restore saved non-secret objects/configuration or recorded Helm revision; verify readiness
and alerts again. Failed datastore/encryption recovery uses the pre-window snapshot and
matching keys under the supported restore flow. Do not add reset flags permanently to
service units or disable admission to force recovery.

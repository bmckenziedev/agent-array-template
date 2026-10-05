# Module: k3s-maintenance (optional)
Optional ordered k3s maintenance kit for backup, encryption, audit and node drains.

## Interface
00-60 scripts consume one rendered node env file at a time. Every script previews unless --i-am-in-the-window is the first argument. lib.sh is sourced only.

## Configuration
Global namespace, identity, runtime, image and cluster network keys follow the organisation configuration.

- `enabled` → `M_K3S_MAINTENANCE_ENABLED`, default `false`.
- `drain_timeout` → `M_K3S_MAINTENANCE_DRAIN_TIMEOUT`, default `300s`.

## Secrets
Names and key names only; see [secrets.required.yaml](secrets.required.yaml). No values are committed.
For configurable bot/identity Secret names, provisioning must use the configured names rather than the defaults.

## Deploy
Enable the module, render node env files, and follow [the runbook](RUNBOOK.md). Preserve previous drop-ins before installing replacements. Commands run locally or in a platform-admin shell; no built-in SSH aliases exist.

## Verify
Run `python -m unittest discover -s modules/k3s-maintenance/tests -t modules/k3s-maintenance` offline.
Render tests use an unmodified canonical fixture; modules are enabled only in an in-memory copy.
Live checks require a platform admin and are never part of offline tests.

## Rollback
Stop the sequence on any failed readiness or quorum check. Restore preserved drop-ins; retain encryption keys until re-encryption and recovery validation pass.

## Security notes
Node roles come from NODE_ROLES_JSON. Drains never force deletion or discard emptyDir data. Encryption rotation is coordinated across every server and remains an explicit runbook step. Stale etcd directories are listed rather than automatically deleted.

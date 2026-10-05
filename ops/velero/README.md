# Velero resource policy

This optional backup-backend configuration excludes private login volumes from Velero.
It is independent of the restic backup configuration.

## Interface

A login-exclusion-policy ConfigMap, protected Namespace and default-deny policy.
The Velero chart and storage backend are installed separately after review.

## Configuration

`org.backup.kind: velero` enables this path. Other backends emit no resources here.
PROJECT_NAME, LOGIN_HOST_ROOT, STORAGE_CLASS_LOGIN and BACKUP_EXCLUDE_JSON set the
namespace and exclusion policy. No new placeholder is declared.

## Secrets

No Secrets are created. Provider credentials remain named, independently provisioned
Secrets in the isolated deployment.

## Deploy

Render and review; the gated Argo Application manages these resources. Follow the
[backend safety procedure](../backup/velero/README.md) before installing a controller
or enabling any backup schedule. Login data is never copied or backed up.

## Verify

`python -B -m unittest discover -s ops/backup/tests -t ops/backup` checks the volume
exclusions and positive/negative render gates. Inspect generated namespace protection,
PSA and default-deny. Controller egress requires explicit endpoint/provider policy.

## Rollback

Revert the desired policy and schedules. Namespace and login storage must not be
pruned. Never restore login credentials from backups.

## Security notes

Host filesystem movers remain disabled. Exclusions and explicit login PV/PVC labels
are mandatory; no policy permits access to another existing system.

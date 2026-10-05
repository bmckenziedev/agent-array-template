# Backup
Core restic-sftp backup of etcd snapshots, sealing controller keys, audit logs and a consistent Grafana SQLite copy.

## Interface
Consumes rendered backup.env and ssh_config.conf. Backup and restore preview by default; `--yes` authorises writes. Weekly checks read without repository locks or persistent caches by default. Exit 0 means success/preview and 1 means refusal/failure.

## Configuration
Global BACKUP_* keys and LOGIN_HOST_ROOT configure the backend and exclusions.

- `staging_dir` Ã¢â€ â€™ `C_BACKUP_STAGING_DIR`, default `/var/backups/cluster-ops`.
- `snapshot_dir` Ã¢â€ â€™ `C_BACKUP_SNAPSHOT_DIR`, default `/var/lib/rancher/k3s/server/db/snapshots`.
- `audit_dir` Ã¢â€ â€™ `C_BACKUP_AUDIT_DIR`, default `/var/log/kubernetes`.
- `grafana_db` Ã¢â€ â€™ `C_BACKUP_GRAFANA_DB`, default `/var/lib/grafana/grafana.db`.
- `sealed_namespace` Ã¢â€ â€™ `C_BACKUP_SEALED_NAMESPACE`, default `kube-system`.

## Secrets
Names and key names only; see [secrets.required.yaml](secrets.required.yaml). No values are committed.
For configurable bot/identity Secret names, provisioning must use the configured names rather than the defaults.

## Deploy
Render with backup.kind=restic-sftp; run `install.sh --yes RENDERED_FILES_DIR`. Provision host secrets from the named Secret through a trusted platform-admin channel. Use 0600 files in /etc/cluster-ops. Initialise restic explicitly; enable the three timers only after a successful run.

## Verify
Run `python -m unittest discover -s ops/backup/tests -t ops/backup` offline.
Render tests use an unmodified canonical fixture; modules are enabled only in an in-memory copy.
Live checks require a platform admin and are never part of offline tests.

## Rollback
Disable timers; retain the encrypted repository and keys. Restore only through [the drill runbook](RESTORE-DRILL.md).

## Security notes
Every BACKUP_EXCLUDE_JSON path, including LOGIN_HOST_ROOT, is passed as an explicit restic exclusion. Missing policy entries cause refusal. No home trees or general PVC roots are selected. Restic encrypts sealing keys in the repository; transient key files are private and removed after each backup. Verify SSH host keys independently.

ssh_config.tmpl is the requested portable template; ssh_config.tmpl.conf is its identical renderer-discovered form. The installer uses the rendered ssh_config.conf. Host Python needs the pinned requirements.txt dependency. Use a dedicated restic repository for these artifacts so latest selects the intended cluster backup.

Recovery-critical cluster credentials are staged privately and encrypted by restic: C_BACKUP_SERVER_TOKEN_FILE / server_token_file (default /var/lib/rancher/k3s/server/token), C_BACKUP_ENCRYPTION_CONFIG / encryption_config (default /var/lib/rancher/k3s/server/cred/encryption-config.json), and C_BACKUP_SECRETS_ENCRYPTION_ENABLED / secrets_encryption_enabled (default true). The flag must match the cluster state; plaintext legacy clusters explicitly set false before an encryption migration. These cluster bootstrap credentials are distinct from interactive vendor login state. A provider-config hash change during snapshot creation causes refusal. Pause timers during coordinated encryption rotation.

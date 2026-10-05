#!/usr/bin/env bash
set -euo pipefail
export MSYS2_ENV_CONV_EXCL='BACKUP_LOGIN_ROOT;BACKUP_SSH_CONFIG'
export MSYS2_ARG_CONV_EXCL='/logins'
root=$(cd "$(dirname "$0")/.." && pwd)
export BACKUP_KIND=restic-sftp BACKUP_LOGIN_ROOT=/logins
export BACKUP_EXCLUDE_JSON='["/logins","/excluded"]'
export BACKUP_SSH_CONFIG=/ssh-config BACKUP_REPOSITORY_PATH=repository
python3 "$root/backup.py" run
if python3 "$root/backup.py" run --yes --exclude /logins; then
  printf 'Exclusion refusal failed\n' >&2
  exit 1
fi
printf 'Backup shell preview/refusal: OK\n'

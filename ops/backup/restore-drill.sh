#!/usr/bin/env bash
set -euo pipefail
umask 077
set -a
source "${BACKUP_ENV:-/etc/cluster-ops/backup.env}"
set +a
exec python3 "$(dirname "$0")/backup.py" restore-drill "$@"

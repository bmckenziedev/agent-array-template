#!/usr/bin/env bash
set -euo pipefail
umask 077
set -a
# Platform-controlled environment is provisioned externally and absent from this template.
# shellcheck source=/dev/null
source "${BACKUP_ENV:-/etc/cluster-ops/backup.env}"
set +a
exec python3 "$(dirname "$0")/backup.py" check "$@"

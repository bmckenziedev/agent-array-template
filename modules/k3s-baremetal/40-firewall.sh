#!/usr/bin/env bash
set -euo pipefail
# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib.sh
source "$(dirname "$0")/lib.sh"
load_env "$@"
: "${SSH_PORT:?missing SSH_PORT}" "${OVERLAY_CIDR:?missing OVERLAY_CIDR}"
[[ ${FIREWALL_PROFILE:-} == overlay ]] || die 'unsupported firewall profile'
echo "Rollback added SSH rule: ufw delete allow ${SSH_PORT}/tcp"
if $yes; then
  [[ ${AA_SSH_CONFIRMED:-} == yes ]] || die 'set AA_SSH_CONFIRMED=yes after testing a second SSH session'
fi
run ufw allow "$SSH_PORT/tcp"
# Role profiles keep the API port off agents; CNI transport is overlay-only.
ports=10250
if server_role; then ports="${APISERVER_PORT:-6443},10250,2379,2380"; fi
run ufw allow in on "$OVERLAY_INTERFACE" from "$OVERLAY_CIDR" to any port "$ports" proto tcp
run ufw allow in on "$OVERLAY_INTERFACE" from "$OVERLAY_CIDR" to any port 8472 proto udp
run ufw allow in on cni0 from "$POD_CIDR" to any port "$ports" proto tcp
printf 'Rollback: ufw delete allow in on %q from %q to any port %q proto tcp\n' "$OVERLAY_INTERFACE" "$OVERLAY_CIDR" "$ports"
printf 'Rollback: ufw delete allow in on %q from %q to any port 8472 proto udp\n' "$OVERLAY_INTERFACE" "$OVERLAY_CIDR"
printf 'Rollback: ufw delete allow in on cni0 from %q to any port %q proto tcp\n' "$POD_CIDR" "$ports"
echo 'Existing firewall rules and enabled state are preserved; enable only after console/SSH verification.'

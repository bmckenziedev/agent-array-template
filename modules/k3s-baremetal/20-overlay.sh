#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"
load_env "$@"
[[ $OVERLAY_KIND == tailscale ]] || { echo 'Use the org overlay provisioning procedure'; exit 0; }
if ! command -v tailscale >/dev/null; then
  echo 'Prerequisite: configure the signed Tailscale OS package repository.'
  run apt-get install --yes tailscale
  if ! $yes; then exit 0; fi
  command -v tailscale >/dev/null || die 'tailscale package unavailable'
fi
if tailscale status --json | grep -q '"BackendState"[[:space:]]*:[[:space:]]*"Running"'; then
  echo 'Tailscale already running; verify identity and overlay address'; exit 0
fi
if $yes; then secure_file "${TS_AUTHKEY_FILE:?set TS_AUTHKEY_FILE}"; fi
run tailscale up "--auth-key=file:${TS_AUTHKEY_FILE:-<0600-authkey-file>}" \
  "--advertise-tags=${TS_TAG_NODES:?missing node tags}" --accept-dns=false

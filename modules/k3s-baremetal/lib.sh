#!/usr/bin/env bash
set -euo pipefail
die() { echo "ERROR: $*" >&2; exit 1; }
yes=false
load_env() {
  [[ $# -ge 2 ]] || die 'usage: script cluster.env node.env [--yes]'
  [[ -r $1 && -r $2 ]] || die 'missing env file'
  # Environment files are trusted executable input, reviewed before staging.
  source "$1"
  source "$2"
  shift 2
  if [[ $# != 0 ]]; then
    [[ $# == 1 && $1 == --yes ]] || die 'unknown option'
    yes=true
  fi
  : "${K8S_VERSION:?missing K8S_VERSION}" "${NODE_NAME:?missing NODE_NAME}"
  : "${NODE_OVERLAY_IP:?missing NODE_OVERLAY_IP}" "${NODE_ROLES_JSON:?missing roles}"
  : "${OVERLAY_KIND:?missing OVERLAY_KIND}"
  [[ $K8S_VERSION =~ ^v[0-9]+\.[0-9]+\.[0-9]+\+k3s[0-9]+$ ]] || die 'invalid pinned version'
  [[ $NODE_NAME =~ ^[a-z0-9][a-z0-9.-]*$ ]] || die 'invalid node name'
  [[ $NODE_OVERLAY_IP =~ ^[0-9a-fA-F:.]+$ ]] || die 'invalid overlay address'
  if $yes; then [[ $(id -u) == 0 ]] || die 'root required'; fi
}
run() {
  printf '%q ' "$@"; printf '\n'
  if $yes; then "$@"; fi
}
secure_file() {
  [[ -f $1 && ! -L $1 ]] || die 'missing regular secret file'
  [[ $(stat -c %a "$1") == 600 ]] || die 'secret file must be 0600'
  [[ $(stat -c %u "$1") == 0 ]] || die 'secret file must be root-owned'
}
server_role() { [[ $NODE_ROLES_JSON == *'"control-plane"'* ]]; }

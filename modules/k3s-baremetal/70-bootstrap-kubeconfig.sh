#!/usr/bin/env bash
set -euo pipefail
[[ $# == 1 && $1 == --yes ]] || { echo 'Bootstrap only: --yes prints the offline escrow procedure'; exit 0; }
[[ $(id -u) == 0 ]] || { echo 'root required' >&2; exit 1; }
echo 'Escrow /etc/rancher/k3s/k3s.yaml into encrypted offline break-glass storage using the org custody procedure.'
echo 'Do not transfer it to laptops. Daily credentials are created with aa login and OIDC.'

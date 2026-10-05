#!/usr/bin/env bash
set -euo pipefail
KUBECONFORM_VERSION='0.6.7'
KUBECONFORM_SHA256='95f14e87aa28c09d5941f11bd024c1d02fdc0303ccaa23f61cef67bc92619d73'
PROMETHEUS_VERSION='3.2.1'
PROMETHEUS_SHA256='a622e3007c9109a7f470e1433cbd29bf392596715cf7eea8b81b37fa9d26b7be'
# Refuse unverified downloads, including during first adoption.
if [[ "$KUBECONFORM_SHA256" == REPLACE_WITH_SHA256 || "$PROMETHEUS_SHA256" == REPLACE_WITH_SHA256 ]]; then
  echo 'Fill both release SHA256 values in install_tools.sh before running.' >&2
  exit 1
fi
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || {
  echo 'Pinned archives support Linux amd64 only.' >&2
  exit 1
}
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
bin="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/agent-array-ci-bin"
mkdir -p "$bin"
curl --fail --location --retry 3 -o "$tmp/kubeconform.tar.gz" \
  "https://github.com/yannh/kubeconform/releases/download/v${KUBECONFORM_VERSION}/kubeconform-linux-amd64.tar.gz"
printf '%s  %s\n' "$KUBECONFORM_SHA256" "$tmp/kubeconform.tar.gz" | sha256sum --check --strict
curl --fail --location --retry 3 -o "$tmp/prometheus.tar.gz" \
  "https://github.com/prometheus/prometheus/releases/download/v${PROMETHEUS_VERSION}/prometheus-${PROMETHEUS_VERSION}.linux-amd64.tar.gz"
printf '%s  %s\n' "$PROMETHEUS_SHA256" "$tmp/prometheus.tar.gz" | sha256sum --check --strict
tar -xzf "$tmp/kubeconform.tar.gz" -C "$tmp" kubeconform
tar -xzf "$tmp/prometheus.tar.gz" -C "$tmp" "prometheus-${PROMETHEUS_VERSION}.linux-amd64/promtool"
install -m 0755 "$tmp/kubeconform" "$bin/kubeconform"
install -m 0755 "$tmp/prometheus-${PROMETHEUS_VERSION}.linux-amd64/promtool" "$bin/promtool"
if [[ -n "${GITHUB_PATH:-}" ]]; then
  printf '%s\n' "$bin" >> "$GITHUB_PATH"
fi
printf 'Installed verified tools in %s; add this directory to PATH locally.\n' "$bin"

# Module: hostwatch (optional)

Hostwatch writes bounded, read-only Ubuntu/Debian health probes to node-exporter's
textfile directory. Checks report independent failures and replace a single metrics
file atomically so incomplete output cannot corrupt a scrape.

## Interface

The systemd oneshot service/timer runs `hostwatch-collect.sh`; metrics cover reboot
requirements, pending security updates, unattended upgrades, RAID/SMART/NVMe,
clock synchronization, k3s certificate expiry, Tailscale overlay-key expiry and
credential expiry annotations. All metric names begin `aa_`. The module's
[PrometheusRule](k8s/hostwatch.rules.tmpl.yaml) consumes those series and monitoring's
`aa:node:info` recording rule. Unsupported checks emit nothing; failed applicable
checks emit an `_up` value of zero. Tailscale is optional; other overlay providers
need a separate read-only adapter.

## Configuration

`modules.hostwatch.enabled` defaults false. `textfile_directory` documents the fixed
default `/var/lib/node_exporter/textfile_collector`; changing it requires coordinating
the service environment and the node-exporter mount. Host paths can be overridden
with HOSTWATCH_ROOT/PROC_ROOT/SYS_ROOT/K3S_ROOT/TEXTFILE_DIR/STATE_DIR. HOSTWATCH_APT_CHECK
selects an apt-check-compatible helper; Debian hosts may need update-notifier-common
or an equivalent helper. HOSTWATCH_LABEL_PREFIX defaults agent-array.example.org;
set it to LABEL_PREFIX in a systemd drop-in so credential annotation keys match.
HOSTWATCH_KUBECONFIG selects the privileged metadata-query identity. The provided
service uses the k3s host kubeconfig only on control-plane nodes.

## Secrets

No credential values are consumed or exported. The optional Kubernetes query
requests only namespace/name and LABEL_PREFIX/expires-at annotations via JSONPath.
However, client-side JSONPath does not limit API authorization: listing Secrets
allows the client to receive their bodies. The source host-admin identity remains
a known gap; prefer a dedicated metadata-only exporter and disable the credential
check where that trust is unacceptable.

## Deploy

Enable the module and render its rules for Argo. Provision bash/coreutils, Python,
openssl, smartmontools, nvme-cli and the apt-check helper with the host baseline.
Run `bash modules/hostwatch/install.sh` to preview, then `--yes` as root on a host.
It installs only the collector and systemd units; it never starts smartd, installs
packages, applies updates, modifies disk metadata or reboots. Configure the annotation
prefix before enabling credential monitoring. Node-exporter already enables the
matching textfile collector in the complete Helm values.

## Verify

```bash
bash modules/hostwatch/tests/test-hostwatch.sh
python -B -m unittest discover -s modules/hostwatch/tests -t modules/hostwatch
```

The fake-host suite exercises healthy/degraded hosts, absent/failing tools,
agent-only hosts, peer-key traps, bounded probes and atomic failure behavior.
Live validation checks node-exporter exposition and permissions without reading
certificate/credential contents. Unit tests use synthetic expiry timestamps.

## Rollback

Disable hostwatch.timer, restore the prior collector/service/timer, reload systemd,
and restore prior rendered rules. Retain the first-seen security-update state when
rolling back versions. Removing metrics intentionally activates collector-stale
alerts while this module remains enabled; disable its rules together.

## Security notes

Root is needed for device health and k3s certificate metadata; systemd uses
NoNewPrivileges, ProtectSystem, ProtectHome and a private temporary directory.
No maintenance action or active disk test is executed. Annotation watches export
only Secret names, namespaces and expiry timestamps. Check-failing/collector-stale
alerts must remain enabled to distinguish unknown status from healthy status.

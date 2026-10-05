# Dashboards

Grafana dashboard JSON and the development ConfigMap renderer retain the source
cluster, fleet, GPU, network, platform and factory views with a node selector.

## Interface

The monitoring plugin emits ConfigMaps labeled grafana_dashboard=1 in NS_MONITORING.
The sidecar loads ConfigMaps only. JSON uses a Prometheus datasource variable and
a multi-select node variable; node hostnames are not fixed in queries.

## Configuration

Namespace and sanitized KSM label placeholders come from the monitoring plugin.
Optional telemetry appears as missing when exporters/modules are absent. These
dashboards are organization-wide; team Viewer access does not isolate metric data.

## Secrets

Dashboard JSON contains no credentials. Grafana authentication uses the Secrets
declared by the parent component.

## Deploy

Edit JSON and run `python -B monitoring/dashboards/render-configmaps.py` to refresh
the review previews. Previews stay outside k8s and are not renderer inputs; the plugin
derives the sanitized KSM label keys. Deploy its ConfigMaps through rendered GitOps output.

## Verify

`render-configmaps.py --check` detects stale review previews. The parent test suite
checks JSON parsing and node variables; browser rendering and query behavior are
live checks.

## Rollback

Restore the previous JSON and regenerate rendered ConfigMaps. Dashboard UIDs remain
stable so updates replace their previous versions.

## Security notes

Imported dashboards are data, not a place for tokens or authenticated endpoint URLs.
Grafana must retain namespaced ConfigMap-only watch permissions.

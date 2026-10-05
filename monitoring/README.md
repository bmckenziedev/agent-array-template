# Monitoring

Prometheus, Alertmanager and Grafana provide organization and tenant observability.
The complete kube-prometheus-stack release uses chart 91.8.2 and retains the source
image digests, scoped operator RBAC and ConfigMap-only Grafana sidecars. Organization
configuration supplies identity groups, alert receivers and quiet hours.

## Interface

The `alertmanager` render plugin emits PrometheusRules, dashboard ConfigMaps, an
Alertmanager configuration ConfigMap and the Helm routing values fragment. Rules
are compiled from [alerts/rules](alerts/rules); the deployed manifests go under
`rendered/global/monitoring/k8s`. ServiceMonitors select Services by
`app.kubernetes.io/name`: litellm, pace, farm-mcp, panel and deployed HTTP MCP servers.
Those Services must expose a named `http` port and `/metrics` (LiteLLM `/metrics/`).
The ported Argo PodMonitor and sealed-secrets ServiceMonitor use named `metrics` ports.

Account and namespace recording rules enrich pace/session metrics with stable account/team labels and
usage-source metadata from the organization directory. MCP metrics follow the
`aa_mcp_requests_total` and `aa_mcp_auth_denied_total` contracts. Factory rules require
`aa_factory_ready`, `aa_factory_inflight`, and coherent snapshot timestamps; tenant
starvation additionally requires the producer's `team` label.

## Configuration

`org.alerting.receivers` declares named webhook, slack, pagerduty, email or ntfy
receivers using `name`, `kind`, `secret`, and `key`. Slack optionally accepts
`channel`; email requires `to`, `from`, `smarthost`, `auth_username`, and uses the
Secret key as its SMTP password. URLs, routing keys and passwords are mounted from
Secrets and referenced with file fields. Ntfy is optional and its Secret URL must
accept Alertmanager JSON (the service's formatter or a compatible adapter).

`default_receiver`, `quiet_hours.start/end/timezone`, and `heartbeat.enabled/secret/key`
come from org.yaml. Critical alerts have zero grouping wait at every hour. Warnings
and unknown severities group for five minutes and are muted during quiet hours;
overnight intervals split at midnight. Info/none are dropped; Watchdog uses its
separate heartbeat route only when enabled. Silence/mute handling retains firing
state, but warnings resolved before quiet hours end do not become queued messages.
Node-down inhibition requires a nonempty node label on both sides. Tenant inhibition
also compares team, account and server to avoid suppressing unrelated tenants.

The [component defaults](org.component.defaults.yaml) declare these overrides:

| Key under components.monitoring | Default | Purpose |
|---|---|---|
| retention_days | 15 | Prometheus retention |
| oidc_auth_path / oidc_token_path / oidc_userinfo_path | /authorize /token /userinfo | Endpoint suffixes relative to OIDC_ISSUER_URL |
| team_receivers | {} | Map team IDs to declared receiver names; unmatched teams use the default |
| litellm_budget_metric | litellm_team_max_budget_metric | Maximum current-period team budget gauge |
| litellm_remaining_budget_metric | litellm_remaining_team_budget_metric | Remaining current-period team budget gauge |
| litellm_team_label | team_alias | Renamed to team in alerts; must match organization team IDs |
| litellm_metrics_secret / litellm_metrics_secret_key | litellm-metrics-key / token | Monitoring-namespace metrics-only key |
| lease_denials_threshold / mcp_auth_denials_threshold | 10 / 10 | Strictly greater than this five-minute count |
| factory_starvation_minutes | 15 | Sustained ready work with no inflight work |

Defaults flatten to `C_MONITORING_<KEY>`. The plugin derives its internal
`C_MONITORING_KSM_LABEL_PREFIX` and `C_MONITORING_KSM_RUNTIME_VM_LABEL` from LABEL_PREFIX
and RUNTIME_CLASS_VM rather than requiring additional org settings.
Kube-state-metrics prefixes exported labels with `label_` and changes punctuation
to underscores: `example.org/role-gpu` becomes `label_example_org_role_gpu`.
The Helm `metricLabelsAllowlist` becomes `--metric-labels-allowlist` and permits only
organization node role/runtime, namespace ownership, and pod ownership labels.
Colliding sanitized label keys can receive KSM conflict suffixes; reserve this
label namespace and verify the exported names before deployment.

GPU/factory/ARC rules are gated by gpu-lanes/factory/arc-ci module enablement.
Kata-GC rules evaluate only present collector series. Hostwatch rules belong to its optional module. Backup absence
rules require an explicit `aa_backup_enabled == 1` signal; present failure/staleness
series remain independently actionable. KSM does not collect Secrets, so legacy
sealing-key rules remain inert until a separate metadata-only exporter supplies
`kube_secret_created`; enabling the Secret collector is not the remediation.

Grafana uses generic OAuth with PKCE, strict role mapping and group-role sync:
GROUP_PLATFORM_ADMIN receives organization Admin, GROUP_AUDITOR and team IdP groups
receive Viewer. Unmatched groups are refused. OIDC issuer endpoints and a redirect
URI `https://<HOST_GRAFANA>/login/generic_oauth` must be registered with the IdP.
Role claims use raw IdP groups, without the API server's OIDC_GROUPS_PREFIX.

## Secrets

[secrets.required.yaml](secrets.required.yaml) names grafana-admin, grafana-oidc,
the optional heartbeat and the metrics-only LiteLLM key. The plugin derives receiver
Secret requirements from org.yaml into
`rendered/files/monitoring/secrets.required.yaml` (complete inventory) and
`rendered/files/monitoring/receiver-secrets.required.json`; these references must be
included in the deployment secret checklist. No receiver values or ciphertext ship.
All receiver Secrets must exist in NS_MONITORING. The sealing helper uses local
kubeseal and a supplied public certificate; hidden input is held briefly in a
mode-0600 temporary file and removed on exit. It never applies a Secret.

## Deploy

Render the organization, review `rendered/global/monitoring/k8s`, and ensure the
named Secrets exist. Run [install.sh](kube-prometheus-stack/install.sh) to print the
full command; use `NS_MONITORING=<configured namespace> ... --yes` to execute it.
Both complete base values and generated routing values are mandatory. The wrapper
saves revision metadata before upgrading and never reuses prior Helm values.
Run the wrapper under Linux/WSL with Python/PyYAML and an executable post-renderer.
Apply the generated rules/dashboards/monitors through the monitoring Argo application.
The generated Alertmanager ConfigMap is reviewable; Helm uses the same configuration
from the routing values fragment. No live installation is part of offline verification.

## Verify

```bash
python -B -m unittest discover -s monitoring/tests -t monitoring
python -B monitoring/kube-prometheus-stack/test_monitoring_rbac.py
python -B monitoring/alerts/validate.py --promtool /path/to/promtool
python -B monitoring/dashboards/render-configmaps.py --check
bash monitoring/tests/test-seal-alertmanager-urls.sh
bash monitoring/alertmanager/validate-config.sh /path/to/alertmanager.yaml
```

Rules/tests are parsed even without promtool, and its absence is reported as skipped.
RBAC fixtures exercise broad inputs and fail-closed drift; `--render` accepts actual
`helm template` output for the pinned chart without contacting a cluster.
Live checks: target discovery, all image pins in Helm output, team budget label
identity, OIDC issuer/claims/role denial and deliberate alert receipt. Namespace
NetworkPolicies must allow Prometheus scrapes, Alertmanager delivery and Grafana IdP
egress. Grafana Admin/Viewer roles control access to the whole organization; team
groups do not provide per-series data isolation.

LiteLLM's [metric reference](https://docs.litellm.ai/docs/proxy/prometheus) documents
maximum and remaining team budget gauges. The rule calculates period spend from
their difference and excludes zero budgets. Verify names and team/alias identity
on the pinned deployment, and initialize budget gauges for inactive teams.

## Rollback

Use `helm rollback kps <saved-revision> -n <namespace> --wait`; revision 0 means a
new release and requires a deliberate uninstall decision. Revert desired rendered
manifests in GitOps and restore the prior complete values. Preserve PVCs and capture
non-secret readiness/target health before and after the change.

## Security notes

Grafana and KSM cannot read Secrets. The operator's cluster permissions are limited
to node/namespace/storage discovery; workload and Secret authority stays in the
monitoring namespace. Kubelet endpoint synchronization is narrowly scoped, except
RBAC cannot resource-name-limit creation. Certgen hooks retain their separate
chart-owned webhook patch permissions. Tenant namespaces never grant operator
Secret reads. Placement uses role labels and all Services are ClusterIP.

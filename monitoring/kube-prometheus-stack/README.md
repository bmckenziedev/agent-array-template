# Kube-prometheus-stack

Chart 91.8.2 supplies the monitoring release with pinned images and a fail-closed
post-renderer that removes broad Secret-reading privileges.

## Interface

The release exposes Prometheus, Alertmanager and Grafana ClusterIP Services.
Full values live in [the Helm template](../helm/kube-prometheus-stack/values.tmpl.yaml).
The generated routing fragment is mandatory for installation.

## Configuration

See [monitoring configuration](../README.md#configuration). The release name kps is
intentionally stable because post-renderer resource names are pinned. Namespace
names are derived from rendered resources. Operator discovery objects live in
NS_MONITORING, even when their scrape targets live in other namespaces.

## Secrets

Grafana credentials and OIDC client credentials use named Secrets. Only the operator's
monitoring-namespace Role can access referenced assets. KSM and Grafana cannot read
Secrets through Kubernetes RBAC.

## Deploy

Run install.sh without arguments to print the full pinned command. `--yes` executes
after saving the prior revision metadata. Ensure the printed namespace matches org.yaml.

## Verify

`python -B monitoring/kube-prometheus-stack/test_monitoring_rbac.py` runs the offline
RBAC suite. `--render <pinned-helm-template.yaml>` additionally validates actual
chart shape; that file must be kept outside the repository and not printed.

## Rollback

`helm rollback kps <saved-revision> -n <namespace> --wait`, then reconcile prior
rendered desired state. PVC preservation and release deletion are deliberate
platform-admin decisions.

## Security notes

Chart/name/argument drift fails closed. Cluster discovery retains only nodes,
namespaces and storageclasses; operator workload mutation stays in monitoring.
Kubelet endpoint CREATE cannot be resource-name limited; other operations are
name-scoped. Admission certgen hook permissions remain separate and chart-owned.

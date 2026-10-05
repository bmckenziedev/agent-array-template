# Alert rules

Custom rules complement kube-prometheus-stack defaults. Rule sources remain outside
the Kubernetes apply directory; the monitoring plugin compiles namespace, component
and KSM label settings into deployed PrometheusRules.

## Interface

`rules/*.yaml` contain PrometheusRule sources; `tests/*.test.yaml` are promtool fixtures.
The Helm release retains its upstream default rules. Custom groups cover node
disk/memory/temperature, GPU health, optional hostwatch, backup/drill, ARC, Argo,
Kata GC, pipeline availability, pace, sessions, MCP, LiteLLM and factory queues.

## Configuration

See [monitoring configuration](../README.md#configuration). Module gates and
absent-safe expressions are implemented by the render plugin. GPU active-node
recording uses organization role labels joined through kube_node_labels, then
Ready/unschedulable state. Cordon is not evidence of intentional node suspension.
Factory idle requires fresh authoritative telemetry, awake GPU state and healthy
model scrapes; it never infers zero work from missing data. Error-ratio alerts require
a minimum request/attempt count to avoid pages from small denominators.

## Secrets

Rules contain no credential values. Legacy sealing-key telemetry needs a dedicated
metadata exporter; the KSM Secret collector remains disabled.

## Deploy

Render and apply only `rendered/global/monitoring/k8s` via Argo. Hostwatch rules
render under the enabled module. No pending scrape manifests are applied directly.

## Verify

`python -B monitoring/alerts/validate.py --promtool /path/to/promtool` stages plain
rule specs and all test fixtures in a temporary directory, checks syntax and runs
every scenario. Without promtool it parses every file and explicitly reports skipped
evaluation. Example nodes in tests do not constrain deployment placement.

## Rollback

Revert desired rendered rule manifests and restore prior complete Helm values when
changing chart defaults. Never remove baseline exporter-health rules to hide missing
telemetry.

## Security notes

Warnings describe observations and do not execute remediation. Unknown usage/power/
queue states remain distinct from healthy or idle states. Alert labels are bounded
identities; prompts, request payloads and credential material must never be labels.

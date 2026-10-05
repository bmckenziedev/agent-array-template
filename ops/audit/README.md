# API audit
Core audit policy and offline probes for per-user attribution without logging credential-bearing bodies.

## Interface
Policy, k3s drop-in and generic kube-apiserver flags. audit_policy_check.py exits 0 on valid expectations, 1 on invariant failure, 2 on invalid input. audit_log_probe.py reads JSONL without printing content.

## Configuration
Global namespace, identity, runtime, image and cluster network keys follow the organisation configuration.

- `log_path` â†’ `C_AUDIT_LOG_PATH`, default `/var/log/kubernetes/audit.log`.
- `retention_days` â†’ `C_AUDIT_RETENTION_DAYS`, default `30`.

## Secrets
Names and key names only; see [secrets.required.yaml](secrets.required.yaml). No values are committed.
For configurable bot/identity Secret names, provisioning must use the configured names rather than the defaults.

## Deploy
Render the policy and flags, validate policy locally, then install through the optional maintenance kit. Restart servers one at a time in an approved window.

## Verify
Run `python -m unittest discover -s ops/audit/tests -t ops/audit` offline.
Render tests use an unmodified canonical fixture; modules are enabled only in an in-memory copy.
Live checks require a platform admin and are never part of offline tests.

## Rollback
Restore the previous policy/drop-in and restart one server at a time. Preserve existing audit logs.

## Security notes
Secrets, ConfigMaps and service-account token requests stay at Metadata in every namespace, including user sessions. Native audit envelopes carry user/groups, namespace and pod. pods/exec, attach and portforward use Request. Commands in query strings can contain sensitive data: prohibit credentials in command-line arguments. An audit policy cannot inject break-glass annotations; the exec guard must supply an audit annotation with the ticket and target user.

## Event source mapping
| Event | Source and correlation |
|---|---|
| Session exec/attach/portforward and snapshot push | API audit Request: OIDC username/groups, namespace, pod, URI/container/command |
| Break-glass | Exec guard audit annotation plus API audit: admin sub, ticket, target user; annotation key uses LABEL_PREFIX/breakglass-ticket |
| Session start/stop and lease grants | Pace stdout event schema: user, SA, account, pod and timestamp |
| API/local model calls | LiteLLM spend logs: alias, user_id, team_id, task_id, model, token counts and cost; prompt storage disabled |
| Seat usage | Vendor CLI telemetry/exporter: user, account and usage window; interactive seats remain per-person |
| In-session actions | Vendor org compliance tooling where offered; verify plan support |
| Panel tasks and factory batch approval/cancellation | Service events: actor sub, team, task/batch, data class and estate |
| MCP calls | Gateway stdout audit: token-derived user, server/tool and outcome; no tool payloads |
| Directory GitOps changes | Git PR author/reviewer and Argo reconciliation events |

The monitoring task ships events to SIEM with user/team correlation and org retention.
Secrets/ConfigMaps are Metadata everywhere, so namespace additions never depend on a stale namespace list.
The probe is read-only; generating ConfigMap/Role probes is a separate approved maintenance action.

`rendered/files/ops/audit/audit-policy.yaml` is API-server startup configuration
(`audit.k8s.io/v1 Policy`), not an API resource. Keep it outside GitOps apply paths.
The exact native document shape is checked by `tools/ci/rendered_assets.py`.
CONNECT subresources retain Metadata and ResponseStarted, so identity, groups,
VAP Audit failure annotations and ticket annotations survive without request
bodies. Wazuh rules 100812/100813 consume these signals; native annotations must
be verified with synthetic positive/negative API-server and vendor logtest evidence.

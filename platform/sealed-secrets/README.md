# Sealed Secrets
Strict-scope encryption keeps credentials out of git while supporting manually reviewed GitOps.
The export contains no controller certificate or encrypted blobs.

## Interface
`seal.sh --namespace-ref llm --name app-env --keys API_KEY --cert <outside-repo-cert>`
or `--namespace <namespace>` prompts silently for every key. Values remain in memory and stdin,
never process arguments, environment exports or logs. Output is
`secrets/k8s/<namespace>/<name>.sealed.yaml`. Existing files are replaced only after successful sealing.
`seal-registry-pull.sh --namespace <namespace> --name registry-pull --registry-host registry.example.org
--cert <outside-repo-cert>` creates a dockerconfigjson Secret with hidden username/token prompts.
Registry hosts are unrestricted by vendor; mint read-only pull credentials appropriate to that registry.

## Configuration
Reads `org.namespaces` for namespace references; rejects `user_prefix`.
The values template consumes `LABEL_PREFIX` and selects control-plane nodes.
The render plugin consumes `PROJECT_NAME`, `APISERVER_ENDPOINT_IPS_JSON`, `APISERVER_PORT` and
`APISERVER_SERVICE_IP`; it grants only the controller pods API and DNS egress in kube-system.
VERIFY chart pod labels and CNI/DNS behaviour against the selected cluster.
Chart 2.20.0, controller 0.40.0 and its image digest are pinned; chart SHA256 is verified before install.
Kubeseal 0.40.0 is the matching CLI baseline (VERIFY its release checksum before installing it).

## Secrets
The controller private key decrypts all organisation blobs. Names only appear in component secret contracts.

## Deploy
Render the repository first. `install.sh` prints its plan; `install.sh --yes` performs the full
`helm upgrade --install ... -f <rendered-values>` with a verified chart. It never reuses stale values.
Keep certificates outside the checkout. Fetch a controller public certificate with kubeseal only
from the intended cluster; pass its path explicitly. Manual-sync Argo owns the resulting blobs.

## Verify
Run `python -m unittest discover -s platform/sealed-secrets/tests -t platform/sealed-secrets -v`
and `bash platform/sealed-secrets/tests/test-seal-registry-pull.sh` offline.
A platform admin must VERIFY controller readiness and a throwaway decrypt round-trip before production use.

## Rollback
Use reviewed previous chart/values pins. Preserve every active controller key across upgrades.

## Security notes
Back up all Secrets labelled `sealedsecrets.bitnami.com/sealed-secrets-key` to an encrypted offline
store with owner-only permissions, never git. Re-back up after automatic key renewal.
Controller key renewal does not rotate upstream credentials or automatically re-encrypt old manifests:
rotate credentials at their issuer, seal replacements, verify consumers, then revoke old credentials.
Recover keys before restoring blobs to a replacement cluster, or re-seal from the credential store.
Strict scope binds each manifest to its namespace and name; never broaden scope for portability.
The scripts do not apply resources or send credentials to vendor APIs.

## External Secrets / Vault alternative
An organisation may instead use External Secrets with Vault or a cloud secret manager.
VERIFY provider authentication, least-privilege per-namespace policies, refresh behaviour and outage
handling before adoption. Keep references in git and credential values in the external store;
remove conflicting SealedSecrets before switching ownership of a native Secret.

Chart 2.20.0's `templates/service.yaml` creates the main and metrics Services
under the single `createController` gate; it has no independent metrics Service
disable key. The separately named `sealed-secrets-platform-metrics` Service is
owned by GitOps and exposes the chart Pod's `metrics` port (8081) in kube-system.
The platform ServiceMonitor selects only `component=platform-metrics`, so the
chart's `component=metrics` Service is not scraped a second time. Chart
`metrics.serviceMonitor.enabled` remains false. The source contract is pinned at
[chart 2.20.0 service template](https://github.com/bitnami-labs/sealed-secrets/blob/helm-v2.20.0/helm/sealed-secrets/templates/service.yaml).

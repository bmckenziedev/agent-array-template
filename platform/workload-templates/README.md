# Workload templates
App-team examples preserve the restricted Pod Security, explicit networking and manual GitOps contract.
Angle placeholders are deliberately not renderer keys. These examples never render or sync automatically.

## Interface
Deployment, Postgres StatefulSet (`db-0`), CronJob, ClusterIP Service, NetworkPolicy,
ServiceMonitor, PrometheusRule, SealedSecret and Argo Application examples.

## Configuration
Replace every `<...>` placeholder after copying selected examples into the app component.
Images must use immutable digests. Choose numeric UIDs compatible with the selected images.
Declare any extra renderer settings in the app's own component defaults.

## Secrets
Secret examples contain only `<sealed by seal.sh>` placeholders. Generate encrypted manifests
with `platform/sealed-secrets/seal.sh`; never replace placeholders with plaintext credentials.

## Deploy
### Onboarding an app namespace
1. Determine whether the namespace is already in `org.namespaces`. Hardening alone owns those
   Namespace objects; do not copy the Namespace example for an existing org namespace.
2. For a new app namespace, review restricted PSA, quota and LimitRange with a platform admin.
   Choose a namespace outside the per-user namespace prefix and maintain enforce-version.
3. Copy selected examples outside this directory, replace every angle placeholder, and remove
   API egress if the app does not need it. Default-deny comes first; grant DNS and explicit
   destinations only. Admit client ingress and Prometheus scrapes with precise selectors.
4. VERIFY storage class, retention/backups, database UID/writable paths, monitoring CRDs/selectors,
   and both ingress and egress sides of every dependency. No hostNetwork, hostPath or external ports.
5. Ask a platform admin to create a least-privilege AppProject separately. The app must not manage
   its own project or broaden its permissions. Point its Application at `rendered/global/<component-dir>`.
6. Render, review the manifest diff and run server dry-runs. Sync manually after positive checks.

## Verify
Run `python -m unittest discover -s platform/workload-templates/tests -t platform/workload-templates -v`.
VERIFY application readiness, restart behaviour, secret mounts and admission with the target cluster.
Restart a test pod after changing NetworkPolicies; re-check egress after endpoint/node IP changes.

## Rollback
Review the previous manifests, preserve PVCs and namespace quotas, and manually sync the selected revision.
Do not prune namespaces or database storage as part of an application rollback.

## Security notes
Every container drops ALL capabilities, forbids privilege escalation, uses read-only rootfs and
RuntimeDefault seccomp. Pod tokens and service links are disabled; resources include ephemeral storage.
Database initialization writes only to the mounted data subdirectory and tmp/run volumes.
ExternalIPs, NodePort and LoadBalancer are prohibited. Broad internet rules are absent.

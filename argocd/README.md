# Argo CD GitOps

The app-of-apps deploys reviewed rendered manifests. Raw templates, host scripts, Helm values and login data never enter its sync paths.

## Interface

The root points to `rendered/global/argocd/k8s/apps`. Each source under rendered/global is manifests only, with recurse and `*.yaml`. users and MCP ApplicationSets generate Applications from reviewed rendered directories, preserve resources when a generated Application disappears, and automate sync with prune/selfHeal. No namespace resources-finalizer is added.

Projects: default is locked (no repos/destinations/resources); platform-policy owns admission/RBAC/secrets; platform-infra owns cluster/runtime/monitoring; services has no cluster-scoped permissions; users is restricted to `USER_NS_PREFIX*`, an explicit namespaced whitelist, and Namespace as its only cluster-scoped kind; modules owns optional module resources. Namespace whitelist cannot restrict Namespace objects by prefix: repo review and platform namespace admission must enforce that additional boundary. Platform projects carry broad permissions and are writable only by platform admins. Projects are bootstrapped and updated manually outside the root to prevent application self-escalation.

## Configuration

Uses org Git remote/revision, namespaces, project/label prefix, OIDC issuer/client ID/group names and HOST_ARGOCD. Helm chart 10.9.6 and source image digests are pinned. Full values file is required on every upgrade. `policy.default: ""` is emitted by the RBAC plugin; Helm `configs.rbac.create: false` prevents Helm from overwriting the generated ConfigMap.

The plugin maps platform admins to admin, auditors to readonly, each team group to get-only on `users/user-<slug>` for its members, and active team leads (OIDC sub) to sync for those same Applications. No team exec, override, create/delete or sync grant exists. Raw IdP team groups and prefixed team groups are both mapped; verify actual token claims. Kubernetes and Argo identity prefixes are different layers.

## Secrets

Secret `argocd-oidc`, key `clientSecret`, must carry label `app.kubernetes.io/part-of: argocd`. Repo credentials are read-only deploy credentials provisioned externally. Local admin is disabled. Test SSO and offline bootstrap recovery before deployment.

## Deploy

Render org files, prepare the namespace via platform/hardening, and provision Secrets. Set ARGOCD_NAMESPACE to the rendered namespace and run `bash argocd/install.sh` to inspect the complete Helm command; `--yes` executes. It uses the full rendered values file and never reuses values. Bootstrap rendered projects, generated argocd-rbac-cm and root manually through the platform admin procedure; the root intentionally excludes its own projects/RBAC ConfigMap.

Waves: 00 hardening (manual), 02 Kata (manual), 05 org directory, 08 cluster/RBAC (manual), 10 secrets (manual), 30 LLM, 35 pace, 40 sessions platform (manual; separate gated Kimi app), 55 MCP, 56 farm MCP, 60 portal (recurses all portal manifests), 70 optional modules (manual), 80 monitoring. Automated infrastructure/service apps do not imply dependencies are healthy: manually gate bootstrap in this order. Parent waves order Application creation/sync, not guaranteed child workload health. Policy and secrets require review and manual sync. The root remains manual.

Helm-only module ARC CI is installed using its module install script and full `rendered/files/modules/arc-ci/helm/` values, outside Argo; no raw Helm values are treated as Kubernetes manifests. Wazuh or other Helm-only workloads follow their module README install procedure; module apps reconcile only the module k8s subtree.

Adding an app: put manifests in a component k8s directory, render them, add a numbered app with the org repo/revision and rendered path, select a least-privilege project/destination, choose a wave and explicit manual/automated policy, test selections, and review the rendered diff. Gate module app directories with RENDER-IF.

## Verify

`python -m unittest discover -s argocd/tests -t argocd` validates plugin permissions, all templates, rendered paths and project boundaries. Live: verify SSO/group/sub claims, admin disabled, lead sync scoped to member apps, auditor readonly, forbidden project destinations, and repo-server connectivity.

## Rollback

Revert rendered Git and sync manually. Avoid force/replace and namespace pruning. Helm rollback must preserve the complete intended values and image pins. Keep bootstrap recovery offline.

## Security notes

ClusterIP only. server.insecure is false; enable it only with a verified access proxy that terminates TLS and exclusive ingress from that proxy, never on a generally reachable server. Platform/hardening owns default-deny. Add narrowly reviewed DNS/IdP HTTPS and repo-server Git egress (SSH 22 or HTTPS 443 to the org Git host) plus internal Argo component flows. A chart ingress policy allowing all would defeat proxy-only ingress because NetworkPolicies are additive. VERIFY these flows before adopting; chart defaults are not an egress security boundary.

Bootstrap order is a manual runbook. Sync waves between Applications are advisory; they do not replace manual completion and verification of prerequisite applications.

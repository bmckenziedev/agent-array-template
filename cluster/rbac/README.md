# Identity RBAC

Group bindings provide platform administration and an explicit auditor resource inventory.

## Interface

`aa-platform-admin` is a ClusterRoleBinding to built-in `cluster-admin`. An admin-level role with a namespace-specific exclusion for pods/exec or pods/attach cannot be expressed using additive Kubernetes RBAC. The sessions exec-guard admission policy must restrict CONNECT requests even for platform admins in labelled user-session namespaces. Verify its failure mode and break-glass ticket controls before granting access.

`aa-auditor` grants only get/list/watch on enumerated resources plus pods/log. It excludes core Secrets, token subresources, exec/attach and services/proxy. Wildcard resources would also grant Secrets, so the baseline list is deliberately explicit. For full discovery coverage, run `refresh-auditor.py --discovery <snapshot.json>` against reviewed API discovery data, review the generated ClusterRole and commit it. Add new CRDs through this process; no automatic wildcard expansion. SealedSecrets and credential-bearing custom resources must also be excluded. ConfigMaps/logs can contain sensitive data; applications must avoid logging credentials.

## Configuration

Uses `OIDC_GROUP_PLATFORM_ADMIN`, `OIDC_GROUP_AUDITOR`, label prefix/project and namespaces. Break-glass group is `OIDC_GROUP_BREAKGLASS`; it has no standing binding. During an incident, issue a time-limited membership and reviewed temporary binding with a ticket, use offline bootstrap custody when identity is unavailable, and remove the binding/membership after the window.

## Secrets

No Secrets are rendered. Offline bootstrap credential custody is required.

## Deploy

Render and manually sync the wave 08 cluster Application only after exec-guard is installed and tested. The initial bootstrap temporarily retains offline admin access.

## Verify

Offline template/RBAC tests. Live: use SubjectAccessReview or impersonated auth checks to verify auditor Secret denial, log reads, and admission denial for admin cross-user exec.

## Rollback

Revert bindings manually; avoid locking out all administration before break-glass custody is tested.

## Security notes

RBAC has no deny rule. Admission, OIDC identity and audit delivery are separate required controls. Audit inventory changes require security review.

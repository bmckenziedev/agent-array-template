# 0014: Namespace per user with label-bound admission

Status: Accepted (design decision D4). Refines [0001](0001-sessions-as-per-user-pods.md); 0001 remains in force.

## Context

RBAC, quota, NetworkPolicy and deletion are namespaced in Kubernetes. Admission rules that list user or namespace names by hand go stale and miss new users.

## Decision

Each user gets one namespace, `<user_prefix><slug>` (example prefix `aa-u-`), that holds all of that user's tools. Admission policies bind by namespace label (`<label-prefix>/kind: user-sessions`), never by a list of names. Each (user, tool, home node) has exactly one login claim.

## Consequences

Per-user RBAC, quota, default-deny and offboarding align with the namespace boundary, and CEL contains no hand-listed names. Onboarding creates more objects, and home-node capacity needs planning. Namespace isolation does not protect against node-root or cluster-root.

Integration refinement: services derive the namespace only after matching the authenticated immutable subject to the directory, and the per-user cluster reader was removed. PersistentVolumes are cluster-scoped, so PV admission selects the login storage class and login labels instead of a namespace selector. CEL cannot look up PVC/PV relationships, so a fail-closed TLS lookup webhook checks claim ownership. Controller and bootstrap writers have narrow named exceptions. Strict rendering refuses automatic home-node selection.

## Alternatives considered

- One shared namespace with per-user labels: rejected because RBAC, quota and NetworkPolicy cannot be scoped cleanly.
- A namespace per user and tool: rejected because it multiplies objects and splits one person's quota.
- Hand-listed namespaces in CEL: rejected because they drift and fail open for new users.

## What would make us revisit it

A need to place one user's tools under different trust domains, or a user count at which per-namespace objects strain the control plane.

## Related files

- [sessions/README.md](../../sessions/README.md)
- [sessions/k8s/user/namespace.per-user.tmpl.yaml](../../sessions/k8s/user/namespace.per-user.tmpl.yaml)
- [sessions/docs/exec-guard-webhook.md](../../sessions/docs/exec-guard-webhook.md)
- [cluster/rbac/README.md](../../cluster/rbac/README.md)
- [docs/MULTI-USER.md](../MULTI-USER.md)

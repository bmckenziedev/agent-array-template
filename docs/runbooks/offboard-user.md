# Offboard a user

Platform admin coordinates identity, vendor, team and storage removal. Offboarded rendering
alone does not revoke live tokens or erase retained volumes. See [policy](../MULTI-USER.md).

## Procedure

1. Record approved offboarding scope and permitted work-retention/export decision. Remove
   IdP/team access and revoke vendor seat sessions; do not rely on pod deletion for revocation.
2. Suspend in users.yaml, render zero replicas, sync and stop active turns/API tasks.
   Revoke attributed virtual keys, delegated grants and outstanding pace leases.
3. Export only permitted work through authorised access. Ordinary admins do not silently
   become the holder; use [break-glass](break-glass.md) if exceptional access is necessary.
4. Set `status: offboarded`, add the slug to tombstones, render and review resource removal.
   Remove stale user RBAC, MCP entitlement and namespace objects through controlled GitOps.
5. Inventory retained PVs and node-local login paths for every tool/home node. Securely
   erase private login storage using the approved storage procedure; no credential backup
   or copy. Delete retained context/results under organisational retention policy.

## Verification and recovery

Prove old OIDC/vendor credentials cannot access sessions, task keys are revoked, replicas
and leases are zero, namespace grants are removed and storage erasure is recorded without
contents. Confirm the permanent tombstone survives rendering.

Pause deletion if legal retention is required; preserve authorised work separately from
login state. Reinstatement is a new reviewed identity/slug and fresh login. Erased homes
are not recoverable; never restore an old login backup.

Suspension: render the suspended user quota, then scale every session StatefulSet to 0. The quota prevents replacement pods; it does not evict existing pods.

Revoke the optional per-user forge Secret (key `token`) with the registry git identity.
Commit author/email come from the registry; never reuse a credential from a login home.

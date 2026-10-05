# Onboard a user

Platform admin and team lead approve membership, data access, tier and one-person vendor
seats. Follow [multi-user policy](../MULTI-USER.md) and [session guide](../../sessions/README.md).

## Procedure

1. Confirm immutable OIDC subject/group claims and choose a new DNS-safe slug not present
   in users or tombstones. Example: Ana in payments, namespace `aa-u-ana`.
2. Add the seat binding in accounts.yaml and user in users.yaml with approved teams,
   primary team, tier, tools and eligible home nodes. Never copy an existing login.
3. Render and run local gates. Inspect RoleBinding subject, namespace labels/annotation,
   quota, runtime, private claim affinity, policy hashes, MCP/context and account access.
4. Review generated diff, commit and sync through approved GitOps. Policies/secrets remain
   manual sync. Ensure required image pull Secrets are kubelet-only, not container mounts.
5. Holder authenticates with OIDC, attaches to own pod and completes the vendor's official
   login flow. Verify organisation workspace restriction with the pinned CLI.

## Verification and rollback

Prove own attach/scale/export works, foreign exec/home/Secret access fails, wrong org login
is refused, pacing leases enforce concurrency, and audit attributes the holder. Use
synthetic context before real repositories. Stop if a VERIFY gate is unresolved.

Rollback by suspending the user, stopping turns and revoking newly granted IdP/vendor
access; reconcile leases/keys. Delete unused private storage only after required work
export and approved erasure. Keep the slug reserved if it has been used; do not reuse it.

Suspension: render the suspended user quota, then scale every session StatefulSet to 0. The quota prevents replacement pods; it does not evict existing pods.

Provision the optional per-user forge Secret (key `token`) with the registry git identity.
Commit author/email come from the registry; never reuse a credential from a login home.

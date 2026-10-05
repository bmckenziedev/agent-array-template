# Rotate secrets

Credential custodian/platform admin follows [sealing](../../platform/sealed-secrets/README.md)
and [required-secret inventory](../../secrets/README.md). No values belong in argv, chat,
Git, status output or this runbook.

## Procedure

1. Inventory affected named Secret/key, consumer and upstream grant. Record non-secret
   revision/rollback scope. Confirm backup/recovery custody before sealing-key changes.
2. Generate a minimally scoped replacement through the upstream authorised flow. Seal via
   protected stdin/input workflow with correct namespace/name; review ciphertext scope.
3. Sync Secret manually before rolling dependent consumers. Use a bounded overlap only
   if the upstream supports it; otherwise schedule downtime. Test before revoking old grant.
4. Revoke old upstream credential and stale task keys; restart clients that cache credentials.
   Inspect logs only for metadata, never dump Secret data or authenticated response bodies.
5. Confirm expiry monitoring and update recovery custody. Preserve sealing keys needed to
   decrypt retained backups; re-seal/re-encrypt through supported procedures before retiring them.

## Verification and rollback

Test new credential with bounded action, old credential denial after revocation and
unchanged consumer entitlement. Master/mint/provider keys retain distinct scopes.
Rollback uses an approved still-valid credential through the same sealing process;
if old grant is revoked, mint another rather than resurrecting leaked material.

Seat login rotation is fresh vendor authentication/revocation in its private home, never
copying/sealing a login. Node moves follow [multi-user lifecycle](../MULTI-USER.md).

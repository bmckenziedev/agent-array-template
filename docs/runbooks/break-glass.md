# Break-glass

Use only for an approved incident/recovery need. It does not transfer a seat to an admin.
Normal platform roles must not silently bypass holder access. See [audit](../../ops/audit/README.md).

## Procedure

1. Record incident ticket, approver, individual admin identity, exact namespace/action,
   reason and expiry. Confirm the validated exec guard and independent audit are available.
2. Grant time-limited break-glass IdP group access. Apply the configured label-prefix
   `/breakglass-ticket` namespace annotation through the reviewed admin path.
3. Use fresh individual OIDC credentials. Perform only recorded actions; never copy
   login files, read tokens into logs or drive seat automation. Preserve metadata evidence.
4. Remove group membership/annotation at expiry or completion, terminate temporary access
   and review audit against the ticket. Revoke affected vendor sessions if exposure occurred.

## Verification and failure handling

Before use, prove ordinary admin/foreign-user CONNECT denial and ticketed-group allowance
on the actual cluster. VAP CONNECT support is VERIFY; a validated webhook is the fallback.
If enforcement/audit cannot be demonstrated, block routine session access and use the
separately authorised cluster recovery procedure. Cluster-root can remove policy; capture
such changes in independent incident evidence.

After removal prove CONNECT denial returns and no temporary grant remains. Rollback is
revocation of the exact temporary grants; do not remove the admission binding.

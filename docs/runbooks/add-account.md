# Add an account

Team lead/platform admin approves vendor contract, classification and capacity. See
[account policy](../MULTI-USER.md) and [pace](../../services/pace/README.md).

## Procedure

1. Choose `seat`, `api` or local `pool`, vendor and plan. A seat has exactly one holder
   and is interactive only. API/pool access uses explicit team/shared-with rules.
2. Add windows, usage source, cap/reserve and concurrency. Example payments API account
   is separate from `acct-claude-seat-ana`; never convert seat logins into shared keys.
3. For API credentials create a scoped vendor project key and sealed Secret in gateway
   namespace using [sealing](../../platform/sealed-secrets/README.md). Configuration stores
   only name/key/namespace. Local pools still need data and attribution policy.
4. Add entitled model groups/team pools. Render, test, inspect route/class restrictions
   and generated required Secrets, then sync credential before gateway/directory activation.
5. For seats assign user/tool/home node and let holder perform fresh official org login.

## Verification and rollback

Test holder-bound usage reporting, lease concurrency/spacing/cap, route omission of seats,
foreign-team/class denial, gateway budget attribution and credential isolation. Set
conservative vendor-side budgets; concurrent telemetry may lag.

Rollback by disabling account consumption, cancelling tasks/leases, revoking vendor keys
and virtual keys, removing references and rendering. Seat revocation and home erasure use
holder/vendor flows; never redistribute the login.

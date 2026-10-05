# Add a team

Platform admin reviews a new IdP group with security and nominated team leads. Follow
[org schema](../../org/README.md) and [gateway](../../llm/README.md).

## Procedure

1. Choose unique team ID and controlled IdP group. Define leads, default tier/permission
   mode, data classes and per-class vendor allowlist. Default CLI approval mode is preferred;
   any bypass mode is an explicit reviewed exception.
2. Define model allowlist, team/task budgets, rate limits and duration, account pools,
   MCP/context access, factory weight/inflight/priority ceilings. Use least privilege.
3. Bind reviewed users and primary-team defaults; declare associated estates. Example Org's
   payments team uses confidential data only with Anthropic/local and approved snapshots.
4. Render, test and review diff. Sync directory, RBAC, per-user policy and gateway teams in
   dependency order; do not activate tasks before budgets/entitlements exist.

## Verification and rollback

Test group claim resolution, team model/budget limits, foreign-team task/key/context denial,
correct primary-team attribution and factory share when enabled. Confirm no access from
an unknown group. Roll back grants by removing membership and entitlements, revoking keys,
cancelling tasks, then regenerating. Retain audit/spend records under policy.

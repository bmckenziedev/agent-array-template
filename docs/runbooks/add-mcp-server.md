# Add an MCP server

Security/platform maintainers approve tools, upstream scope and classification before
registry enablement. Read [MCP/context](../MCP-AND-CONTEXT.md) and [MCP templates](../../mcp/).

## Procedure

1. Prefer in-image stdio for local read-only data; choose HTTP for central upstream access.
   Declare unique name, clients, teams, timeout and read/write authority in registry.
2. Pin reviewed image/command and dependencies. HTTP servers implement audience-bound
   TokenReview, namespace/user resolution, per-call entitlements, metadata audit and
   `aa_mcp_*` metrics. Stdio tools inherit sandbox authority and require no remote credential.
3. Declare HTTP Service/deploy and exact upstream egress; add DNS/API checks only as needed.
   For example tickets permits payments and `tickets.example.org`, with named
   `mcp-tickets-upstream` held only by the server pod.
4. Seal required upstream credentials and document them in secrets.required.yaml. Never
   put values in registry env, rendered ConfigMaps or session pods.
5. Add team/context references, render per-CLI config, inspect tenant/server policy and
   policy-merge hashes. Run offline suites and strict artifact validation, then pilot.

## Verification and rollback

Prove entitled success, wrong-audience/expired/foreign-team denial, revoked entitlement,
undeclared egress denial and long-lived token refresh. Confirm untrusted text wrapping,
payload-free logs and bounded write permissions. Kimi remains MCP-free.

Rollback by removing registry/team grant, regenerating tenant config/egress and restarting
affected sessions after work export. Disable server ingress, revoke upstream grants and
remove unused deployment/Secret. Do not leave stale managed configuration or broad routes.

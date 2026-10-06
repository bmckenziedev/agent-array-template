# 0017: MCP pod identity in v1

Status: Accepted (design decision D7). Refines [0006](0006-mcp-pod-identity-auth.md); 0006 remains in force.

## Context

Sessions need attributable MCP access without distributing secrets into pods. Both supported CLIs must work. Per-user upstream ACLs would need a token broker, which is substantial extra work.

## Decision

MCP authentication in v1 is pod identity. Each session receives an audience-bound projected ServiceAccount token; the server validates it with TokenReview and maps the namespace to the user and teams from the organisation directory. Upstream credentials stay in the server pod. A per-user token broker is phase 2.

## Consequences

No secret distribution is needed. Upstream systems see a server identity, not the individual, so team service credentials do not reproduce per-user upstream ACLs. Every HTTP server must implement TokenReview, entitlement and audit itself (see [0018](0018-no-central-mcp-gateway-v1.md)).

Integration refinement: allowed projected audiences are exactly `<project>-mcp`, `-pace`, `-supervisor` and `-supervisor-hook`, each expiring within 3600 seconds; MCP tokens mount only into the CLI container, and no API-server audience is accepted. `aa-mcp-token` emits one JSON object of string Authorization headers. Codex native `bearer_token_env_var` is read only at process start, so the Codex `aa-mcp-bridge` rereads the projected token on every request with bounded input/output. Claude loads entitled servers through `managed-mcp.json` exclusive control. The bridge, token refresh and the TokenReview bound pod-name extra remain VERIFY.

## Alternatives considered

- Static per-user or per-team bearer secrets: rejected because they need distribution, rotation and storage in sessions.
- Forwarding the user's OIDC token: rejected because sessions would hold a broad identity token.
- A per-user upstream token broker now: deferred to phase 2 because it is real extra work and needs its own ADR.

## What would make us revisit it

An upstream that must enforce per-person ACLs, or an approved broker design.

## Related files

- [mcp/README.md](../../mcp/README.md)
- [mcp/servers/_template/README.md](../../mcp/servers/_template/README.md)
- [sessions/common/bin/aa-mcp-token](../../sessions/common/bin/aa-mcp-token)
- [sessions/common/bin/aa-mcp-bridge](../../sessions/common/bin/aa-mcp-bridge)
- [docs/MCP-AND-CONTEXT.md](../MCP-AND-CONTEXT.md)
- [docs/CONTRACTS.md](../CONTRACTS.md)

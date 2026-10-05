# 0006: MCP pod-identity authentication

## Context

Sessions need attributable MCP access without distributing upstream credentials. Direct per-user grants require a token broker not present in v1.

## Decision

Project short-lived MCP-audience ServiceAccount tokens; each HTTP server uses TokenReview, namespace identity, org-directory and rendered entitlements per call. Upstream credentials stay server-side. No central gateway in v1; per-user-token auth is rejected pending a broker ADR.

## Consequences

Every server needs reviewed auth/RBAC/egress/audit implementation. Team service credentials do not reproduce upstream individual ACLs. CLI token refresh and managed paths are VERIFY; revoked entitlements and wrong-audience tests are pilot gates.

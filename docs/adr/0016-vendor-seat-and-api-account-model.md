# 0016: Vendor seat and API account model

Status: Accepted (design decision D6). Consolidates [0004](0004-org-seats-not-consumer-plans.md) and [0005](0005-seats-interactive-automation-on-api-accounts.md); both remain in force.

## Context

The source deployment used consumer plans, and its own terms-of-service notes ruled out pooled or hosted personal seats. An organisation needs a contractual and identity boundary for each person and a separate path for unattended work.

## Decision

Interactive use runs only on organisation seats: one person per seat, interactive only, with login pinned to the organisation workspace through vendor managed settings. Automation runs only on organisation API accounts through the LiteLLM gateway. Kimi (vendor `moonshot`) is disabled by default and has no MCP in v1.

## Consequences

Seats never appear in automation routes, and seat allowance is never pooled. Each vendor needs legal and data approval before enablement; configuration is not approval. Automation has separate budgets, keys and attribution. Kimi needs separate legal clearance and has no vendor-managed MCP lock, so it gets no MCP ConfigMap.

Integration refinement: API-key automation uses a separate policy without `forceLoginOrgUUID` or `forceLoginMethod`. Codex requirements pin the allowed workspace only when it is nonempty, and the managed `sandbox_mode` was removed. Kimi enforcement is a wrapper/hook-managed organisation lock, which is not a vendor managed configuration, with a plain-login fallback note. The gateway defaults to the LiteLLM OSS tier (member role user); enterprise features need `components.llm.enterprise_license`. Workspace-lock and login-pin behaviour on pinned CLIs remain VERIFY.

## Alternatives considered

- Consumer or personal plans hosted in pods: rejected by vendor terms and because the organisation cannot revoke or audit them.
- Pooled seats shared between people or used by automation: rejected because they break per-person attribution and vendor seat terms.
- A subscription-to-API shim: rejected because it crosses the credential and vendor-policy boundary.

## What would make us revisit it

Vendor terms that change seat or automation rules, a vendor-managed policy lock for Kimi with MCP support, or legal approval of a new vendor (which requires its own ADR).

## Related files

- [llm/README.md](../../llm/README.md)
- [sessions/README.md](../../sessions/README.md)
- [sessions/kimi/image/README.md](../../sessions/kimi/image/README.md)
- [sessions/codex/image/README.md](../../sessions/codex/image/README.md)
- [org/org.example.yaml](../../org/org.example.yaml)
- [docs/CONTRACTS.md](../CONTRACTS.md)

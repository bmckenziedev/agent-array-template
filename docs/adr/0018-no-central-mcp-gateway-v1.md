# 0018: No central MCP gateway in v1

Status: Accepted (design decision D8). Refines [0006](0006-mcp-pod-identity-auth.md); 0006 remains in force.

## Context

A central MCP gateway could centralise authentication, entitlement and audit, but it is another privileged component to build, test and operate before any server exists.

## Decision

v1 has no central MCP gateway. Each server enforces authentication, entitlement and audit through the shared contract, and each has its own NetworkPolicy. A gateway is a follow-up ADR.

## Consequences

The design is smaller and testable offline now. Every server must implement the contract correctly, so review effort is per server; the server template carries the reference implementation. Because servers already share one contract, a gateway can be inserted later without changing clients.

Integration refinement: per-server egress policies are named `mcp-egress-<server>` in both user and server outputs. The factory MCP URL follows the factory module gate. `server_egress` to external hosts needs a proven FQDN-capable CNI policy or an exact-host gateway, because ordinary NetworkPolicy cannot express host names.

## Alternatives considered

- A central gateway in v1: rejected as more work than the first servers need, and a single point of failure.
- Per-server auth without a shared contract: rejected because servers would diverge and a later gateway would not be a drop-in.

## What would make us revisit it

Enough servers that per-server review becomes the bottleneck, a need for cross-server rate limits or policy, or the broker work in [0017](0017-mcp-pod-identity-v1.md).

## Related files

- [mcp/README.md](../../mcp/README.md)
- [mcp/servers/_template/README.md](../../mcp/servers/_template/README.md)
- [mcp/servers/arrayops/README.md](../../mcp/servers/arrayops/README.md)
- [mcp/k8s/server/networkpolicy.per-mcp.tmpl.yaml](../../mcp/k8s/server/networkpolicy.per-mcp.tmpl.yaml)
- [docs/runbooks/add-mcp-server.md](../runbooks/add-mcp-server.md)
- [docs/MCP-AND-CONTEXT.md](../MCP-AND-CONTEXT.md)

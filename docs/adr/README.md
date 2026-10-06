# Architectural decisions

Each ADR records context, decision and consequences. These are accepted export design
decisions, not proof of deployed enforcement. New boundary/contract changes use the next
number and explicitly supersede earlier decisions; see [contributing](../../CONTRIBUTING.md).

- [0001: Sessions as per-user pods](0001-sessions-as-per-user-pods.md)
- [0002: Kata for session isolation](0002-kata-for-session-isolation.md)
- [0003: Rendered manifests for GitOps](0003-rendered-manifests-gitops.md)
- [0004: Organisation seats rather than consumer plans](0004-org-seats-not-consumer-plans.md)
- [0005: Interactive seats and API automation](0005-seats-interactive-automation-on-api-accounts.md)
- [0006: MCP pod-identity authentication](0006-mcp-pod-identity-auth.md)
- [0007: Sealed secrets by default](0007-sealed-secrets-by-default.md)
- [0008: Admission guards and dry-runs](0008-admission-has-guards-and-dry-runs.md)
- [0009: PSA levels per namespace](0009-psa-levels-per-namespace.md)
- [0010: Optional modules](0010-optional-modules.md)
- [0011: Fresh export tree without source history](0011-fresh-export-tree-without-history.md)
- [0012: Single organisation configuration surface](0012-single-org-config-surface.md)
- [0013: Rendered output layout for GitOps](0013-rendered-output-layout.md)
- [0014: Namespace per user with label-bound admission](0014-namespace-per-user.md)
- [0015: Node-local encrypted login storage](0015-node-local-encrypted-login-storage.md)
- [0016: Vendor seat and API account model](0016-vendor-seat-and-api-account-model.md)
- [0017: MCP pod identity in v1](0017-mcp-pod-identity-v1.md)
- [0018: No central MCP gateway in v1](0018-no-central-mcp-gateway-v1.md)
- [0019: Pace as a lease service](0019-pace-lease-service.md)
- [0020: Permission mode per team](0020-team-permission-modes.md)
- [0021: CONNECT admission guard for session access](0021-connect-exec-guard.md)
- [0022: Hashed sanitization inventory](0022-hashed-sanitization-inventory.md)
- [0023: Namespace ownership](0023-namespace-ownership.md)
- [0024: Action and tool checksum pinning](0024-action-and-checksum-pinning.md)
- [0025: Apache-2.0 licence as a placeholder](0025-license-placeholder.md)

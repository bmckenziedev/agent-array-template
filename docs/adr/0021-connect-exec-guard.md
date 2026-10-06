# 0021: CONNECT admission guard for session access

Status: Accepted (design decision D11). Refines [0008](0008-admission-has-guards-and-dry-runs.md); 0008 remains in force.

## Context

Ordinary cluster administrators can usually exec into any pod. "Only the seat holder reaches their sessions" (R5) therefore needs an admission control on CONNECT requests, not RBAC alone. ValidatingAdmissionPolicy support for CONNECT was unproven when the decision was taken.

## Decision

A CONNECT admission policy on `pods/exec` and `pods/attach` allows only the holder, or a break-glass actor when the namespace carries a ticket annotation, and every use is audited. A webhook was documented as the fallback; VAP CONNECT support was marked VERIFY.

## Consequences

Administrative ownership no longer implies session access, and break-glass is visible and ticketed. Node-root and cluster-root remain trusted: they can remove the policy. Rollout depends on positive and negative server dry-runs.

Integration refinement: the webhook fallback for CONNECT was dropped. The `aa-session-exec` VAP handles CONNECT directly and also matches `pods/portforward`, only in namespaces labelled `kind: user-sessions`. It reads the namespace `oidc-sub` and `breakglass-ticket` annotations, requires the exact prefixed holder or prefixed break-glass membership with a nonempty ticket, guards optional fields with `has()`, and binds with Deny and Audit. The TLS lookup webhook now handles storage only. Exec/attach into the supervisor container is holder-only; ticketed break-glass console actors may observe, interrupt or stop, never spawn, type or decide. Audit policy keeps metadata without CONNECT bodies, and the Wazuh rules alert on VAP Audit violations and break-glass CONNECT. CONNECT dry-runs remain VERIFY.

## Alternatives considered

- RBAC only: rejected because cluster administrators bypass it.
- A CONNECT webhook: kept as fallback at decision time, then superseded by the VAP during integration; it adds a TLS service in the access path.
- No break-glass path: rejected because recovery needs an audited exception.

## What would make us revisit it

Server dry-runs showing the target API server does not evaluate VAP on CONNECT, which would require a reviewed webhook ADR, or a need for time-bounded tickets enforced in admission.

## Related files

- [sessions/docs/exec-guard-webhook.md](../../sessions/docs/exec-guard-webhook.md)
- [sessions/k8s/aa-session-exec.tmpl.yaml](../../sessions/k8s/aa-session-exec.tmpl.yaml)
- [sessions/README.md](../../sessions/README.md)
- [ops/audit/README.md](../../ops/audit/README.md)
- [docs/SUPERVISION.md](../SUPERVISION.md)

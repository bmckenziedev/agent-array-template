# Incident first response

On-call opens a ticket, assigns incident authority and records scope/actions. Use
[threat model](../SECURITY.md), [audit](../../ops/audit/README.md) and [monitoring](../../monitoring/README.md).

## Procedure

1. Classify suspected identity, credential, context, budget, runtime, host or control-plane
   compromise. Capture minimal non-sensitive metadata: events, image digests, policy
   revisions, audit actor/target/outcome and alert timeline. Never collect prompts or tokens.
2. Contain through approved scope: suspend affected user/tasks, stop factory dispatch,
   disable connector/MCP ingress or gateway account route. Preserve evidence before deletion
   when practical; do not blindly restart a compromised workload.
3. Revoke affected IdP sessions, vendor/API grants and task keys. Assume login exposure
   after host/root compromise; revoke and require fresh authentication. Never copy homes
   for forensic convenience. Rotate sealing/recovery keys if their custody was exposed.
4. For tenant incidents test cross-user reachability. For controller/host incidents assume
   wider scope: quarantine the node/control path, review RBAC and independent audit, and
   rebuild trusted artifacts rather than trusting the suspect process.
5. Restore known-good configuration/data through [recovery](restore-drill.md), clear
   boundary tests and permit one synthetic task before staged resumption.
6. Review cause, corrective ADR/tests, affected data obligations and credential revocation
   evidence. Coordinate private disclosure using [policy](../../SECURITY.md).

## Guardrails and completion

Emergency session access follows [break-glass](break-glass.md). Do not disable admission,
default-deny or audit as routine containment. Cluster-root removal of a guard is a separate
incident action requiring independent evidence. Avoid broad firewall deletion; use targeted
additive containment that preserves recovery connectivity.

Close only after affected credentials are unusable, trusted state restored, classification
and account boundaries retested, paging works and incident authority approves resumption.

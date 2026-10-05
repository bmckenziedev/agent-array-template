# 0008: Admission guards and dry-runs

## Context

CEL access to absent optional fields can fail ordinary workloads or invalidate intended enforcement. Ready objects do not demonstrate new admission behavior.

## Decision

Guard every optional read with has(), lint rendered CEL, scope bindings by namespace labels, and prove positive plain Pod/Deployment plus relevant exact session and negative server dry-runs before Deny/Fail activation. VERIFY CONNECT support or use a validated webhook.

## Consequences

Policy rollout has an explicit evidence and rollback gate. The plain baseline Pod has no initContainers, ports or volumes; session shapes remain separately constrained. Preserve prior non-secret policies for rollback without deleting protection wholesale.

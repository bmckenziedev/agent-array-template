# 0009: PSA levels per namespace

## Context

Some system or host-monitoring workloads cannot run under restricted PSA, while unlabelled namespaces create bypass paths.

## Decision

Label every namespace with explicit enforce level/version. Restricted is the baseline; document exceptions and retain restricted warn/audit when relaxed. Hardening declares core namespaces, sessions declares user namespaces, and modules baseline their additional namespaces.

## Consequences

Exceptions remain trusted host/control paths requiring scoped RBAC and review. Blanket restricted enforcement is not a substitute for testing required controllers; relaxed levels are never an automatic exemption from admission or default-deny.

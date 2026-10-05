# 0002: Kata for session isolation

## Context

Coding tools handle untrusted context and may execute processes. Container policy alone shares a host kernel with other workloads.

## Decision

Use a verified microVM runtime for interactive sessions and designated risky workloads. Apply non-root, read-only filesystem, capability and seccomp controls inside the guest. Require configured runtime/placement contracts rather than assuming a label proves isolation.

## Consequences

Isolation costs memory/startup capacity and requires virtualisation support and cleanup verification. Host controllers and guest escape remain risks. Separate CI/session nodes when assurance requires it; never fall back to privileged host execution to restore availability.

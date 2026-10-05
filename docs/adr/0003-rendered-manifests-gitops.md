# 0003: Rendered manifests for GitOps

## Context

Tenant and account policy must produce a reviewable reproducible desired-state diff without a privileged cluster rendering plugin.

## Decision

Author registries, defaults, templates and deterministic plugins; commit generated rendered outputs. Argo CD reads rendered manifests only. Policy and secret apps use manual sync, and namespaces have no destructive resources finalizer.

## Consequences

Generated files enlarge diffs and need regeneration checks. Non-manifest files must remain outside Argo manifest roots. Rendering and policy review are release gates; handwritten generated edits are overwritten.

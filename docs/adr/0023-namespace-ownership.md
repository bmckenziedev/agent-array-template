# 0023: Namespace ownership

Status: Accepted (design decision D13). Refines [0009](0009-psa-levels-per-namespace.md); 0009 remains in force.

## Context

When several Argo applications emit the same Namespace, they fight over its labels and deletion. PSA levels spread across components become hard to review.

## Decision

Platform namespaces are created only by platform hardening, and its PSA table is the single place for their levels. Modules own their additional namespaces.

## Consequences

Each Namespace object has exactly one Argo owner, and core PSA levels are reviewed in one table. A new `org.namespaces` key needs an explicit PSA entry or rendering fails.

Integration refinement: the ownership rule covers every namespace outside the hardening table, not only modules. Sessions owns per-user namespaces; the cluster component owns the isolated `<project>-login-storage` namespace (privileged enforce for its hostPath helper, restricted warn/audit, default-deny); `ops/velero` owns its namespace when `org.backup.kind` is `velero`; modules such as wazuh, pkg-mirror and arc-ci own theirs, with project-prefixed names. The GitOps contract requires `Prune=false,Delete=false` on every Namespace object. Owners outside the table set explicit PSA labels on their Namespace objects and keep restricted warn/audit when enforcement is relaxed.

## Alternatives considered

- Each component creates the namespaces it uses: rejected because shared namespaces would have several owners.
- One component owns all namespaces including modules: rejected because disabled modules must emit nothing.

## What would make us revisit it

A namespace shared by several optional modules, or Argo features that make multi-owner Namespace objects safe.

## Related files

- [platform/hardening/README.md](../../platform/hardening/README.md)
- [platform/hardening/render_plugin.py](../../platform/hardening/render_plugin.py)
- [cluster/login-storage/README.md](../../cluster/login-storage/README.md)
- [ops/velero/k8s/namespace.tmpl.yaml](../../ops/velero/k8s/namespace.tmpl.yaml)
- [modules/README.md](../../modules/README.md)

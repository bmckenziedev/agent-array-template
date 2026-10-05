# Upgrade

Platform admin coordinates k3s/Kubernetes, charts, runtimes and pinned CLI images. Review
[cluster](../../cluster/README.md), [GitOps](../../argocd/README.md) and component Verify sections.

## Procedure

1. Read supported upgrade/skew and artifact contracts for the exact targets. Inventory
   current non-secret versions, Helm revisions, pins, policy/bindings and firing alerts.
   Obtain tested backup/restore proof and export ephemeral work.
2. Build/scan CLI and service images, verify dependency/download hashes and registry
   digests. Update authored org image/CLI pins together with admission/managed-policy
   expectations. Placeholder or tag-only pins block deployment.
3. Render deterministically and run all local gates. Chart upgrades use pinned artifacts
   and full explicit `-f` values; never `--reuse-values` or secret-bearing output capture.
4. Pilot vendor workspace locks, managed MCP syntax/paths, token renewal and permissions
   against pinned binaries. Recheck gateway edition features and data agreements.
5. Perform positive plain Pod/Deployment and relevant exact session/negative server
   dry-runs before changed admission binding. Validate CRD/API compatibility before charts.
6. Follow the distribution's supported control-plane then worker sequence in
   [maintenance window](maintenance-window.md), one failure domain at a time. Roll a
   two-user pilot before broader session rollout.

## Verification and rollback

Check API/node/runtime health, fresh and existing own login, cross-user denial, pacing,
gateway/MCP/context, audit and baseline alerts. Save evidence without identifiers/secrets.
Stop on unexpected denial, isolation loss or quality regression.

Restore prior authored pins/generated manifests and exact saved policy objects; Helm
rollback uses the recorded release revision. Cluster/datastore downgrade may require
snapshot recovery rather than binary reversal: follow supported restore procedure.
Never fall back from isolated runtime to privileged host execution to complete an upgrade.

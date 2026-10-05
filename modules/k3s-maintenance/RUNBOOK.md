# Maintenance window
1. Announce a window, validate quorum, disk space, node readiness and rollback access.
2. Run scripts without flags first. Node inventory is the rendered per-node env files, not a host list.
3. Confirm an off-host backup and complete a scratch restore drill before changing servers.
4. On each server enable secrets encryption with `20-secrets-encryption.sh --i-am-in-the-window NODE_ENV`.
   Preserve encryption configuration in encrypted restic storage. Never delete old encryption keys early.
5. Restart one server at a time, waiting for readiness and matching encryption hashes across all servers.
   Follow k3s prepare/rotate/re-encrypt commands for the installed version; validate each phase across servers.
   Scan an isolated snapshot with `lib/etcd-secrets-scan.py` before retiring old keys; do not print secret values.
6. Render and validate the audit policy, install the audit drop-in, restart one server at a time, and
   run the audit log probe after platform-admin probe requests. Monitor log growth and retention.
7. Cordon and drain one node; avoid forced deletion or loss of emptyDir data. Resolve blocked PDBs explicitly.
8. Reboot locally, confirm Ready, validate workloads and runtime isolation, then uncordon. Repeat per node.
9. Keep stale etcd copies until backup and recovery validation passes. Deletion is a separate reviewed action.
10. Close the window with event summaries only. Roll back a failed drop-in from its preserved copy;
    encryption rollback requires retaining every key still needed for stored data.

# Login storage

A dedicated local-path instance stores each login in a separate retained PVC for one (user, tool, node). Logins are never copied between nodes or backed up because refresh-token rotation and account isolation make credential sharing unsafe.

Decision record: [0015: Node-local encrypted login storage](../../docs/adr/0015-node-local-encrypted-login-storage.md).

## Interface

The provisioner lives in `<project>-login-storage`, uses its own provisioner name, ConfigMaps and service account, and coexists with distribution local-path storage. The nodePathMap has no fallback path and includes only session nodes. StorageClass topology and helper nodeSelector require the role-sessions label. PVCs use ReadWriteOnce: ReadWriteOncePod is CSI-only, and local-path is not CSI. RWO does not serialize multiple pods on the same node; sessions must cap Codex concurrency and replicas at one for each login.

## Configuration

Uses `LOGIN_HOST_ROOT`, `STORAGE_CLASS_LOGIN`, session nodes, label prefix, API endpoint/Service IPs/port and the isolated login-storage namespace. WaitForFirstConsumer preserves scheduling/node affinity; Retain prevents automatic credential deletion. Login images use UID 1000; setup directories are 0700 owned by 1000. The parent is root-owned 0711. Verify session UID compatibility before deployment.

## Secrets

Login files are holder credentials created interactively inside the pod. No Secret object or backup contains them. Exclude the entire login root from backups, snapshots, image capture and support bundles. Encrypt the underlying node volume with dm-crypt/LUKS and manage keys outside Git. Cloud volume encryption alone does not pass the host ancestry check; an alternative attested encryption procedure requires explicit org approval.

## Deploy

Run rendered `prepare-login-root.sh --require-encrypted` first; add `--yes` only on
new isolated adoption nodes. Pre-bound local PVs do not invoke dynamic setup: run
`bash rendered/files/cluster/node-prep/<node>/prepare-login-homes.sh` on each declared home
node, inspect the dry-run plan, then add `--yes`. The script requires root, checks the
node name, refuses symlink ancestors and creates root-owned 0711 parents plus UID/GID
1000-owned 0700 login/projects/sessions directories. It never copies login material.
Prepare new directories before session pods mount subPaths; do not run this against
existing services during adoption. Dynamic setup also pre-creates both transcript dirs.

The dedicated login-storage namespace enforces PSA privileged for helper hostPath and
CHOWN/DAC_OVERRIDE/FOWNER, with warn/audit restricted and default-deny. Helpers need only
the selected login path, no host network/PID or privileged container. The provisioner
is non-root with read-only rootfs and dropped capabilities.

Image versions are pinned to registry manifest digests: [upstream release v0.0.32](https://github.com/rancher/local-path-provisioner/releases/tag/v0.0.32), manifest digest verified directly from Docker Registry; busybox 1.36.1 is also pinned. The upstream release does not publish the image digest in its notes. Revalidate architectures and release pins before adoption.

## Verify

Offline tests assert Retain, WaitForFirstConsumer, topology and pins. Live: a PVC on a non-session node must remain unprovisioned; a holder PVC binds to the configured home node, survives pod deletion, and cannot migrate. Verify helper admission, UID permissions and API egress under default deny.

## Rollback

Revert manifests without deleting retained PVs or paths. Disable new provisioning before replacing the provisioner.

## Security notes

Offboarding: suspend the user and revoke IdP/vendor sessions; scale holder pods to zero and verify none still mount the PVC; record the exact PV nodeAffinity and local path; delete the PVC while preserving the Retain PV; mount-check that path, verify its resolved path is within LOGIN_HOST_ROOT and not a symlink, then perform an approved node-local wipe; verify absence and delete the released PV record. SSD overwrite is not a reliable secure erase: vendor revocation plus encryption-key destruction on retirement is required. Never wipe a whole login root for one user. Teardown intentionally refuses deletion so a StorageClass policy change cannot erase logins silently.

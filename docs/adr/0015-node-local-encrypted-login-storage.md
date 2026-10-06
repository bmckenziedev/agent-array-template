# 0015: Node-local encrypted login storage

Status: Accepted (design decision D5).

## Context

Vendor logins rotate refresh tokens, so a copied login can invalidate the original or let two places use one seat. Earlier source descriptions also disagreed on whether logins lived on tmpfs or disk.

## Decision

Logins live on an encrypted node disk through a dedicated node-local StorageClass. They are never backed up or copied. A tmpfs option exists for organisations that prefer RAM-only logins. A reboot keeps a disk login; moving to another node requires a fresh login.

## Consequences

Holders log in again only after a node move or, with tmpfs, a node restart. Offboarding requires an explicit node-local wipe because Retain PVs survive namespace deletion. Encryption, backup exclusion and mount attestation become platform provisioning duties.

Integration refinement: the provisioner runs in an isolated `<project>-login-storage` namespace with its own provisioner name, ConfigMaps, service account and API egress, and coexists with distribution local-path storage. Claims are Retain, WaitForFirstConsumer and ReadWriteOnce; RWO does not serialise pods on one node, so each login is capped at one replica. `prepare-login-root.sh --require-encrypted` checks dm-crypt/LUKS ancestry; pre-bound PVs use generated `prepare-login-homes.sh`, which creates 0700 directories owned by UID/GID 1000 and never copies login material. Session pods omit fsGroup to keep 0700. Entrypoints refuse a filesystem that does not match `AA_LOGIN_STORAGE`. The Velero module ships a resource policy that excludes login volumes.

## Alternatives considered

- tmpfs only: rejected as the default because every node restart forces a new login.
- Network or replicated storage: rejected because it moves login material off the node and into other backup paths.
- Kubernetes Secrets for logins: rejected because logins are interactive holder state, not platform-provisioned credentials.

## What would make us revisit it

Vendor support for device-bound or non-rotating session credentials, or an attested encrypted volume type that cannot be backed up or migrated.

## Related files

- [cluster/login-storage/README.md](../../cluster/login-storage/README.md)
- [cluster/render_plugin.py](../../cluster/render_plugin.py)
- [sessions/README.md](../../sessions/README.md)
- [ops/velero/README.md](../../ops/velero/README.md)
- [ops/velero/k8s/login-resource-policy.tmpl.yaml](../../ops/velero/k8s/login-resource-policy.tmpl.yaml)

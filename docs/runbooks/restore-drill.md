# Restore drill

Recovery custodian/platform admin uses an isolated target with production access blocked.
Define RPO/RTO and cadence, then verify before destructive maintenance. See
[backup implementation](../../ops/backup/README.md).

## Coverage contract

Recover consistent datastore snapshots, cluster config/CA/encryption material, sealing
keys, durable pace/gateway databases and required service PVCs. Git supplies reviewed
rendered desired state. Inventory every new durable component explicitly; raw database
volume copies are not automatically application-consistent backups.

Never back up or restore vendor login homes. Workspaces, caches and image/model downloads
are rebuildable; approved exported patches/results follow their own retention. Keep
backup access/decryption credentials in independent recovery custody, not only on the
failed cluster. Protect snapshots and key exports as secrets.

## Procedure

1. Select snapshot ID, obtain recovery keys through authorised custody and verify encrypted
   repository integrity. Restore into a private temporary target; never print key contents.
2. Check file inventory, permissions and absence of login paths. Verify sealing keys,
   datastore snapshot integrity and parseable consistent database dumps.
3. Build compatible isolated cluster/runtime/storage using supported provider procedures.
   Restore datastore with matching encryption keys; restore controller sealing keys before
   testing a synthetic sealed object. Never persist cluster-reset flags in a service unit.
4. Restore service data/dumps and reviewed manifests in [bootstrap order](../ADOPTION.md).
   Keep external vendor dispatch, CI triggers, connectors and notifier delivery disabled
   to prevent duplicate tasks or production identity collisions.
5. Verify synthetic seal/decrypt, pace/gateway persistence, OIDC/RBAC, admission negatives,
   runtime isolation and application data consistency. Seat holders perform fresh logins
   only after an authorised real cutover, never from restored credentials.
6. Record snapshot/version, integrity checks, measured RPO/RTO and unresolved gaps without
   sensitive contents. Destroy temporary plaintext/key material under custody policy.

## Acceptance and recovery failure

A drill passes only when required state is usable and boundaries hold. Repository listing
or backup job success alone is insufficient. Alert on stale/failed drills and missing
sealing keys. On failure retain protected evidence, stop maintenance/cutover, select a
known-good snapshot or repair the documented gap, then repeat. Test targets never join
production unintentionally. Real disaster cutover requires incident authority and an
explicit plan for credentials, fresh logins and resuming work without duplicate dispatch.

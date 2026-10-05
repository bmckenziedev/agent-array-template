# Restore drill
1. Verify restic credentials and the configured login exclusion list on an isolated recovery host.
2. Run `weekly-check.sh` to verify repository data without writing repository locks or a persistent cache.
   The scheduled `--yes` check takes the normal repository lock.
3. Run `restore-drill.sh` to preview, then `restore-drill.sh --yes`; this creates a new private scratch directory.
4. Check snapshot readability with the matching etcd tools; verify sealing certificate fingerprints
   against the approved key inventory, audit log continuity and Grafana SQLite integrity.
5. Record only artifact hashes and success/failure. Never print controller private keys or login material.
6. A platform admin separately approves a production recovery: stop the control plane, follow the
   distribution's snapshot restore procedure, restore controller keys before reconciling SealedSecrets,
   and restore Grafana during a write pause. Scratch restoration never performs these steps.
7. Securely remove scratch key material after review; rotate recovery credentials according to org policy.

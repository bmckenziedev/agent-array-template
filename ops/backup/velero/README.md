# Velero alternative
Optional backup backend; not rendered while restic-sftp is selected. The example disables node-agent and snapshots.
Use the login resource policy with `velero backup create --resource-policies-configmap login-exclusion-policy`.
All login PVCs must exclusively use the configured login StorageClass. Also label login PVCs and PVs
`velero.io/exclude-from-backup=true`; exclude every user-session namespace from each backup schedule.
No host filesystem backup is permitted: resource policies cannot safely filter arbitrary host paths.
The exclusion list including LOGIN_HOST_ROOT remains the mandatory organisational policy.
Validate generated Backup objects and policy attachment before enabling any snapshot or filesystem data mover.

Validate a local PV inventory with check_resource_policy.py before enabling a schedule. The policy records the complete exclusion list as an annotation and enforces the login StorageClass skip; local and hostPath login PVs must also carry the explicit exclusion label. Velero has no general host-path prefix condition, so filesystem movers remain disabled. The alternative policy is rendered explicitly from its template when adopting Velero; the parent restic gate does not auto-deploy it. See [Velero resource filtering](https://velero.io/docs/main/resource-filtering/) for supported conditions.

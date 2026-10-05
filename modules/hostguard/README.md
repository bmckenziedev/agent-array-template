# Module: hostguard (optional)

An additive host INPUT guard blocks pod TCP access to SSH and etcd while preserving existing firewall authority. It is an optional host-side control, separate from Kubernetes NetworkPolicies.

## Interface

`hostguard.py --env <rendered-env>` prints an nft draft. `--report` inventories the existing ruleset. `--check` inventories and checks syntax without mutation. `--apply --yes` checks and atomically creates the previously absent `inet aa_hostguard` table. Exit status 0 means success; 1 means validation or nft failure; 2 means invalid arguments.

## Configuration

`modules.hostguard.enabled` defaults to false. `modules.hostguard.pod_interfaces` defaults to `[cni0]` and renders as `M_HOSTGUARD_POD_INTERFACES_JSON`. Select actual pod-facing interfaces for the installed CNI; an empty list uses pod-source matching only. `POD_CIDR` comes from the org cluster configuration. The generated `hostguard.env` is parsed as data, never sourced. Additional actual IPv6 ranges may be supplied with repeated `--pod-cidr` arguments.

## Secrets

None. Ruleset inventory and rollback files belong in restricted administrative storage, outside git.

## Deploy

Enable the module and render the org. On each selected Linux node, use the rendered `files/modules/hostguard/hostguard.env` with the CLI. Rendering and Argo do not apply host firewall rules. Review the draft and hook priorities, keep an out-of-band console and a second administrative connection, then run `--check`. Apply explicitly with `--apply --yes` only after review. Existing marked tables require separately reviewed reconciliation; this tool refuses automatic replacement. Foreign tables using the owned name are always refused.

## Verify

Offline: `python -m unittest discover -s modules/hostguard/tests -t modules/hostguard -v`. Tests use mocked nft and synthetic traffic; they never change host rules. VERIFY on Linux: nft syntax and transactional exclusive creation, real CNI interfaces and pod CIDRs, local pod SSH/etcd denial, administrative SSH, API, kubelet, metrics, DNS and ICMP/PMTU reachability. Inventory readiness and firing alerts before and after rollout.

## Rollback

For a newly created table only, a platform admin may run `nft delete table inet aa_hostguard`. Preserve other tables and concurrent firewall changes. Restore a previously owned table only from a reviewed table-scoped transaction. No timer, persistence or automatic rollout is installed.

## Security notes

The INPUT hook runs at priority -10 before normal filter priority 0. TCP 22/2379/2380 from configured pod-facing interfaces or pod-source CIDRs is dropped, including established connections. Other traffic proceeds through existing chains. The renderer never flushes tables, inserts a blanket ACCEPT, or changes forwarding/NAT. Exclusive creation prevents racing into an existing table after inspection.

Masqueraded remote pod traffic may appear as a node source and bypass these predicates. Overlay ACLs, route authority and source-node egress enforcement remain necessary. Confirm CNI packet paths and hook priorities; this narrow guard does not provide a complete host perimeter.

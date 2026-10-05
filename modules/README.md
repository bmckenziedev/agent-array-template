# Optional modules

All modules start disabled. Enable individually after README verification/security gates.
Disabled subtrees render nothing. Additional namespaces retain PSA/default-deny.

| Module | Purpose | Enable key | Guide |
|---|---|---|---|
| factory | Approved bounded work, deterministic checks and weighted scheduling. | `modules.factory.enabled` | [README](factory/README.md) |
| gpu-lanes | Pinned local-model serving with measured context/concurrency limits. | `modules.gpu-lanes.enabled` | [README](gpu-lanes/README.md) |
| session-jobs | API-account task runners with credential-free test execution. | `modules.session-jobs.enabled` | [README](session-jobs/README.md) |
| pkg-mirror | Controlled dependency caches for bounded build/test egress. | `modules.pkg-mirror.enabled` | [README](pkg-mirror/README.md) |
| wazuh | Security telemetry with explicit host-access exclusions. | `modules.wazuh.enabled` | [README](wazuh/README.md) |
| hostwatch | Host health, expiry and runtime monitoring. | `modules.hostwatch.enabled` | [README](hostwatch/README.md) |
| seccomp-gvisor | Additional syscall/runtime isolation for selected workloads. | `modules.seccomp-gvisor.enabled` | [README](seccomp-gvisor/README.md) |
| hostguard | Reviewed host controls and additive firewall changes. | `modules.hostguard.enabled` | [README](hostguard/README.md) |
| hermes-ops-chat | Scoped operations chat using organisation API accounts. | `modules.hermes-ops-chat.enabled` | [README](hermes-ops-chat/README.md) |
| workstation-lane | Optional external compute with explicit availability windows. | `modules.workstation-lane.enabled` | [README](workstation-lane/README.md) |
| arc-ci | Sandboxed CI runners with reviewed trust groups. | `modules.arc-ci.enabled` | [README](arc-ci/README.md) |
| k3s-baremetal | Bare-metal cluster/provider provisioning integration. | `modules.k3s-baremetal.enabled` | [README](k3s-baremetal/README.md) |
| k3s-maintenance | Gated maintenance, recovery and upgrade helpers. | `modules.k3s-maintenance.enabled` | [README](k3s-maintenance/README.md) |

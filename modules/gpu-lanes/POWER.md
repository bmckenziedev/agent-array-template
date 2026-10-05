# GPU power management

Power caps are an optional host-level operations choice. Measure throughput, thermal behavior and energy use for each qualified model before choosing a cap; no device model or wattage is assumed by these templates.

Platform admins inspect supported limits with `nvidia-smi --query-gpu=uuid,power.min_limit,power.max_limit,power.limit --format=csv`. Record the original limit and choose a value within the reported bounds. Apply through a reviewed host service or maintenance procedure, not a privileged Kubernetes DaemonSet. Use stable GPU UUIDs when multiple devices exist.

Repeat the factory bench gate with the selected cap and monitor temperature, power/thermal violation durations and throughput. Restore the recorded limit on regression. Unsupported power controls must fail explicitly; do not change capabilities or reset the GPU to work around them.

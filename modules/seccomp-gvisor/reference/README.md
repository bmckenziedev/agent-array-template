# RuntimeDefault reference

`containerd-2.3.4-runtime-default-dropall.json` records containerd 2.3.4's default filter for an x86_64 container with all capabilities dropped. It contains syscall policy data, not workload secrets. Treat it as a versioned reference fixture. VERIFY against the target runtime after upgrades; do not assume every distribution emits this filter.

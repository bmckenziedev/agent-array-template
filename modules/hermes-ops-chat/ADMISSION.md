# Admission verification
Before applying the binding, perform server dry-runs against the rendered Deployment, and plain
busybox:1.36 Pod and plain Deployment in an unrelated namespace, without optional fields.
All three must be admitted. Also verify a minimal pod in NS_OPS without the chat ServiceAccount is unaffected.
Negative chat-SA cases must be rejected by this policy: wrong runtime, command override, hostPath,
foreign Secret/PVC, additional init/ephemeral container, privilege escalation and runtime annotations.
Use `kubectl --kubeconfig OIDC_CONFIG apply --dry-run=server -f CASE` and inspect the policy name in denial.
The namespace selector and chat-SA match condition keep unrelated workloads outside this policy.

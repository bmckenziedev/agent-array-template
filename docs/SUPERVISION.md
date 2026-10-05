# Supervision extension

The supervisor component is absent; Part B integration is skipped. Its current contract
is described in [contracts](CONTRACTS.md). Keep separate PID namespaces; do not implement
the earlier shared-PID design. A sidecar-private control socket is reached through
kubectl exec -c supervisor -- aa-supervise; only a request-only permission socket is
shared. No TCP/port-forward control listener or console credential Secret is allowed.

Credentialed email/HMAC notifications require a relay outside session pods. No relay
is provided here; notifier adapters are an organisation extension point with named
Secrets, bounded retries, audit, redaction and synthetic heartbeat/delivery checks.

## Deploy-phase checks

After implementation, authorised admins must prove positive/negative admission,
holder-only spawn, read-only transcript subPaths, container token audience separation,
rotation/TokenReview and the private unix socket on Memory emptyDir under the pinned
Kata version. Confirm that estate containers cannot observe CLI process memory and that
CLI entrypoints do not assume another container PID 1 is their process. No live proof
is claimed by this export.

# Supervisor notifiers

Credential-free adapters deliver redacted events through the supervisor egress wrapper.

## Interface

Plugins implement `Plugin(config, ctx)` and `notify(event)`, returning delivered/detail.
The context exposes bounded `post_json` and audit. Plugins do not open sockets directly.

## Configuration

Configure `components.supervisor.notifiers` and exact `hook_egress` CIDR/port pairs.
Retries are bounded by one timeout budget and cannot extend permission deadlines.

## Secrets

SMTP, HMAC and other delivery credentials belong to an external organisation relay.
No credentialed relay is included in this template. The relay must TokenReview the
`<project>-supervisor-hook` token, check the session identity and deduplicate requests.
Never replay the console token or mount relay credentials into a session pod.

## Deploy

Provide the approved external relay extension before enabling credentialed delivery.
Register a credential-free HTTPS endpoint and inspect rendered egress policy.

## Verify

Run the supervisor offline suite with synthetic delivery and heartbeat cases. Verify
relay authentication, deduplication and credential rotation during isolated adoption.

## Rollback

Remove the adapter configuration and render again; revoke relay credentials separately.

## Security notes

Every payload passes redaction. Delivery failures are audited and do not authorise work.
See [supervisor](../../README.md) and [protocol](../../PROTOCOL.md).

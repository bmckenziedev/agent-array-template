# 0010: Optional modules

## Context

Local compute, runners and host integrations have different prerequisites and risk from the session/API platform.

## Decision

Keep factory, lanes, jobs, mirrors, host/security tooling, ops chat, CI and provider/maintenance integrations as independently disabled modules. Enable through modules.<name>.enabled with component defaults, README, tests and explicit dependencies.

## Consequences

Core adoption stays smaller; disabled modules render nothing. Both gate states and all-module rendering need tests. Alternatives considered: retired GPU serving and tmpfs login-home unit; avoid carrying retired implementations as default platform requirements.

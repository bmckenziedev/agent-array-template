# 0020: Permission mode per team

Status: Accepted (design decision D10).

## Context

In the source, the CLI permission mode was a personal choice. With write-capable MCP servers, an unprompted tool call can change shared systems, so the choice needs organisational ownership.

## Decision

Permission mode is set per team and defaults to the vendor default. `bypassPermissions` is opt-in per team; it is never the organisation default.

## Consequences

Teams take responsibility for their mode, and reviewers see mode changes as registry diffs. CLI modes never override admission, managed deny rules, Kata, data gates, egress or account rules; these remain the hard floor in every mode.

Integration refinement: `policy.permission_modes_allowed` is the catalogue and `policy.default_permission_mode` the default; removing `bypassPermissions` from the catalogue forbids it everywhere. `USER_PERMISSION_MODE` resolves through the user's primary team, then the policy default. Each mode maps to native CLI settings: Kimi has no verified accept-edits equivalent and keeps manual approval. The mapping is VERIFY on each pinned release. When supervision is enabled, permission requests follow tiers (automatic, policy engine, human) through the managed `aa-permission` server; bypass modes skip that routing but not cluster guardrails.

## Alternatives considered

- Per-user choice: rejected because write-capable tools affect shared systems.
- A single organisation-wide mode: rejected because teams have different risk.
- Bypass by default: rejected because it removes the only per-action prompt.

## What would make us revisit it

A verified change in vendor permission semantics, or supervision policy that makes per-team modes redundant.

## Related files

- [sessions/README.md](../../sessions/README.md)
- [org/README.md](../../org/README.md)
- [org/teams.example.yaml](../../org/teams.example.yaml)
- [services/supervisor/README.md](../../services/supervisor/README.md)
- [docs/SUPERVISION.md](../SUPERVISION.md)

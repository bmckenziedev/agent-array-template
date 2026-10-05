# 0005: Interactive seats and API automation

## Context

An unattended dispatcher must not consume human seat allowance or move seat authentication into another harness.

## Decision

Preserve R1-R5: official unmodified vendor CLI, private unmoved login, holder-started turns, per-account limits and holder-only access with audited break-glass. Headless/scheduled automation uses scoped API accounts through the gateway. Never route Claude Code to non-Claude models.

## Consequences

Automation has separate budgets and keys; factory outputs require deterministic checks and review. Seats never appear in automation routes. Alternatives considered: subscription API shim; rejected because it crosses the credential and vendor-policy boundary.

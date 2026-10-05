# 0004: Organisation seats rather than consumer plans

## Context

Employee access, data processing and revocation need an approved commercial and identity boundary. A successful login does not establish contractual permission.

## Decision

Use one approved organisation seat per person and enforce vendor workspace restrictions on pinned CLIs. Exclude consumer plans and pooled seat logins. Enable each vendor only after legal/data review.

## Consequences

Seat provisioning and offboarding become explicit administrative tasks. Workspace-lock behavior remains VERIFY until tested. Organisation terms and vendor settings require periodic review; configuration alone is not legal approval.

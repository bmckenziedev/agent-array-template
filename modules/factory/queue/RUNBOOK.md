# Queue operations

Team members submit through the authenticated factory HTTP API or factory MCP.
A team lead reviews the immutable batch and approves through /v1/batches/<id>/approve.
Approval copies and pins the selected estate snapshot. Status and results remain
team-scoped. Cancellation belongs to the submitter or team lead.

When backpressure rejects a batch, finish or cancel active work before submitting
more. Investigate quarantined inputs without editing the approved snapshot. A new
submission starts a new approved lifecycle. Never restart a frontier session from
a queue action. The engine always runs as a supervised foreground pod process.

Workstation pull and return commands are described in README.md. A failed transfer
stays quarantined for review; it is never silently retried. Imported results must
pass the same gate inside the factory before publication.

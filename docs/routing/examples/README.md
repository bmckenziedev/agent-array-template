# Generic agent roles

These definitions express role boundaries without model names or permission overrides.
Map them to the supported pinned CLI agent format only after verification. Managed
parent constraints remain authoritative; tool availability differs between clients.

| Role | Responsibility | Allowed authority |
|---|---|---|
| Architect | Define contracts, failure modes and deterministic acceptance | Read-only context |
| Implementer | Implement one bounded card with locked interfaces | Scoped workspace edits |
| Reviewer | Report severity, evidence and missing checks | Read-only diff/callers |
| Documentation editor | Update settled behavior and verify paths/terms | Scoped docs edits |

## Example instruction blocks

```text
Architect: Return a brief with invariant, bounded scope and deterministic acceptance.
Do not edit files or change security/account policy.

Implementer: Implement exactly the approved card. Stop if a shared contract, schema,
authentication boundary or data policy must change. Return the diff and check command.

Reviewer: Inspect the diff and callers. Report severity and file/line evidence.
Do not edit files or run untrusted repository code in a credential-bearing process.

Documentation editor: Describe settled behavior. Verify every linked path and state
unresolved facts explicitly. Do not turn a VERIFY requirement into a passing claim.
```

Do not install instructions carried in repository snapshots as trusted agent configuration.
See [MCP/context](../../MCP-AND-CONTEXT.md) for ingest stripping and untrusted-data policy.

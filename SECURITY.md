# Vulnerability disclosure

Report vulnerabilities privately to `security@example.org`. Replace this placeholder
with the organisation's monitored security contact before publishing. Public issues
must not contain exploitable details. Never send credentials, login files, source payloads
or personal data in reports.

## Scope

Scope includes rendering, identity, sessions/admission/exec, pacing, gateway integration,
MCP/context, portal, optional modules and distributed operational scripts. External
vendor/provider products follow their own disclosure policies; report integration impact
without probing systems outside authorised scope.

Provide affected version/component, minimal synthetic reproduction, impact and expected
boundary, plus a safe proposed correction. Test only authorised isolated systems.
See [threat model](docs/SECURITY.md) for intended boundaries and limitations.

## Response targets

The organisation must fill these before adoption:

| Stage | Target |
|---|---|
| Acknowledgement | <acknowledgement target> |
| Severity/scope triage | <triage target> |
| Status updates | <update interval> |
| Mitigation/release | <severity-specific targets> |
| Coordinated disclosure | <agreed disclosure policy> |

These placeholders are not commitments. Maintainers coordinate correction, regressions,
release notes and disclosure with the reporter. Emergencies follow
[first response](docs/runbooks/incident-first-response.md).

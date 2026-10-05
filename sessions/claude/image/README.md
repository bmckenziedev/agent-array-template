# Claude session image

Official pinned vendor CLI for one seat holder in a Kata session pod.

## Interface

Entrypoint roles: `claude`, `estate`, `check` and `usage`. Helpers use the `aa-` prefix.
Managed policy and its `.rendered.sha256` manifest are mandatory. The login mount is private.

## Configuration

Build from repository root: `docker build -f sessions/claude/image/Dockerfile .`.
`{{CLAUDE_CLI_VERSION}}` documents the corresponding build ARG; its default equals
npm's unchanged lockfile version. A version change requires a reviewed lockfile update.
`WITH_FACTORY_MCP=1` copies factory MCP/index when both are present; `0` omits them.
`AA_LOGIN_STORAGE=disk` requires a persistent disk mount; `tmpfs` requires tmpfs.
`AA_PERMISSION_MODE`, `AA_ACCOUNT`, `AA_USER`, `AA_HOME_NODE` and pace settings come from the pod.

## Secrets

No credentials are built into the image. Login uses the official vendor flow inside the pod.
Registry credentials are referenced by the pod's image pull secret.

## Deploy

CI builds and pushes a digest; rendered StatefulSets select that digest through Argo CD.

## Verify

Run the sessions offline tests. Verify vendor organisation enforcement and managed MCP support
against the pinned CLI before enabling the vendor in an adopting organisation.

## Rollback

Select the previous reviewed image digest and policy together; retain the same login claim.

## Security notes

Refuse injected credentials/endpoints, unverified policy, unrendered MCP configuration and
login directories whose uid or mode differs from the exact 0700 contract. No login backups.

Each Remote Control listener has capacity one because one pace lease accounts for one
session. `AA_MAX_SESSIONS` is an account ceiling, not a listener capacity target.
Higher capacity requires a verified vendor hook acquiring and releasing each session lease.
`AA_CLAUDE_MCP_MODE=managed-file` uses managed discovery; `cli-flag` passes the hashed
managed MCP file with `--mcp-config` and `--strict-mcp-config`. An absent MCP entitlement
uses an empty temporary MCP configuration. VERIFY both flags on the pinned Remote Control CLI.

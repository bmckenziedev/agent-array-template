# Codex session image

Official pinned vendor CLI for one seat holder in a Kata session pod.

## Interface

Entrypoint roles: `codex`, `estate`, `check` and `usage`. Helpers use the `aa-` prefix.
Managed policy and its `.rendered.sha256` manifest are mandatory. The login mount is private.

## Configuration

Build from repository root: `docker build -f sessions/codex/image/Dockerfile .`.
`{{CODEX_CLI_VERSION}}` documents the corresponding build ARG; its default equals
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

Codex login, status and TUI operations take a nonblocking local flock on the login mount.
This serializes credential refresh even if pace is unavailable and leases fail open.
The managed workspace sandbox remains enabled; unavailable guest sandbox support requires
platform review, and any full-access mode requires explicit team permission.

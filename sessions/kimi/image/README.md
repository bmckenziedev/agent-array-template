# Kimi session image

Official pinned vendor CLI for one seat holder in a Kata session pod.

## Interface

Entrypoint roles: `kimi`, `estate`, `check` and `usage`. Helpers use the `aa-` prefix.
Managed policy and its `.rendered.sha256` manifest are mandatory. The login mount is private.

## Configuration

Build from repository root: `docker build -f sessions/kimi/image/Dockerfile .`.
`{{KIMI_CLI_VERSION}}` documents the corresponding build ARG; its default equals
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

The egress role reads `kimi-egress-denied-cidrs` managed ConfigMap, refuses empty
exclusions, checks exact CONNECT hosts and matching TLS SNI, and logs source Pod IP
without request content. It caps active tunnels at eight per source Pod IP. Kimi's
one-Pod-per-user admission bound makes this a per-user cap while Pod IPs are preserved.
VERIFY CNI source-IP preservation and audit enrichment from Pod IP to namespace/user;
NAT would combine callers and must be corrected before rollout.

Kimi has no vendor managed config in v1. The wrapper rewrites config.toml at each
start and pod env overrides it; hook-based deny checks enforce the local policy,
not CLI [[permission.rules]]. MCP is disabled in v1: no Kimi ConfigMap is emitted.
Future MCP support targets $KIMI_CODE_HOME/mcp.json (mcpServers), never config.toml
or kimi mcp add. Only the managed file could satisfy the workspace guard.
Use kimi login --region global; fall back to plain kimi login if rejected. Headless
API examples must never combine -p with --auto/--yolo/--plan; managed
 default_permission_mode="auto" supplies the default instead.

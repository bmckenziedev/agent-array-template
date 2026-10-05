# Sessions

Sessions host official vendor CLIs in Kata microVMs. Each user owns one namespace and a separate
StatefulSet, seat account and home-node login claim for each enabled tool. Isolation, managed
policy, admission and default-deny egress remain mandatory.

## Interface

The holder uses `aa sessions`, `aa work remote`, snapshots and Kubernetes exec through OIDC. Session
pods expose no Kubernetes Service. Claude Remote Control names are `aa-<user>-<node>-<n>`.
Cross-vendor hand-offs consist of files; a vendor session never drives another seat. `aa-rc` and
`aa-codex` acquire and renew pace leases before starting a listener or TUI, and release them on
exit.

### Governing rules

- R1: official, unmodified, pinned CLIs authenticate with the user's organisation seat through the vendor flow inside the pod.
- R2: login material never leaves its `(user, tool, home node)` directory. It is never copied, exported, backed up or moved to another node.
- R3: seats serve interactive, user-started sessions only. Queues, cron, webhooks, dispatchers and unattended work use API accounts.
- R4: pace leases enforce each account's concurrency, spacing, usage cap and reserve. HTTP 409 prints the reason and refuses startup. An unreachable pace service warns and permits startup only when lease fail-closed is disabled.
- R5: only the seat holder reaches sessions. Platform administrators require audited break-glass with a ticket annotation and the break-glass OIDC group. Administrative ownership does not grant ordinary exec access.

### Object model

The optional [session supervision sidecar](../docs/SUPERVISION.md) adds holder control, redacted output and permission routing through Unix sockets. It shares the CLI uid and a dedicated Memory tmux socket volume while retaining separate PID namespaces, transcript-only login mounts and a private control mount. Automation may observe or stop, never spawn or type.

A namespace has kind `user-sessions`, user and team labels and an OIDC subject annotation. A holder
RoleBinding references that subject. Each enabled `(user, tool)` has a policy ConfigMap, one
StatefulSet pinned to its home node and a login PVC labelled with the same user and tool. The seat
account is not shared between holders. A retained node-affine PV backs each home directory.
Namespace selection uses labels, never a list of user names. Cluster-scoped PV admission selects
user-login labels and the login storage class because PVs have no namespace. The fail-closed lookup
webhook verifies the claim namespace belongs to user sessions, namespace identity and tool ownership
of mounted PVCs, and break-glass ticket ownership for exec. CEL handles structural checks; lookups
require the webhook.

The CLI, policy init container, snapshot receiver (`estate`) and Codex usage reporter have distinct
mounts. Estate never mounts a login claim. Usage receives only the Codex `sessions` subdirectory
read-only. Projected tokens have MCP or pace audiences, expire within one hour and mount only in
their allowed containers.

### MCP and context consumption

Optional `<tool>-mcp` ConfigMaps provide rendered fragments and a hash manifest. `aa-policy-merge`
sets the Claude server allowlist exactly, preserves managed-only restrictions and produces
`.rendered.sha256`. Missing fragments mean no MCP; malformed fragments refuse startup. Entrypoints
validate the merged policy hash and refuse project MCP configuration. Kimi has no MCP ConfigMap in
v1.

`context-policy` holds entitled estates; absent policy refuses snapshot pushes. Snapshot ingest
strips vendor configuration, login data and project MCP files. Snapshot file fallback transfers only
entitled, stripped snapshots and never copies login material; helper diagnostics never print
credential or transcript contents. `aa-mcp-token` supplies a projected pod-identity token; factory
MCP uses the same identity and is installed under `/opt/factory` when enabled. MCP entitlement and
data-class restrictions come from the organisation directory, not the pod's assertions. Usage
reports contain rate-limit readings only, never transcript text.

## Configuration

Organisation keys select vendor enablement, pinned images, login storage class and root, runtime
class, home nodes, seat accounts, permission modes, tiers, egress CIDR exceptions and lease
fail-closed behavior. Namespace names and identity come from normalised user entities. Kimi renders
only when `org.vendors.moonshot.enabled`; factory egress renders only when
`org.modules.factory.enabled`.

`admission_ca_bundle` (`C_SESSIONS_ADMISSION_CA_BUNDLE`) defaults to an empty string. VERIFY and
configure the base64 CA bundle matching the webhook serving certificate before admission rollout;
empty trust is not a deployable configuration.

Component defaults are `claude_mcp_mode: managed-file` (`C_SESSIONS_CLAUDE_MCP_MODE`) and
`usage_report_interval_s: 300` (`C_SESSIONS_USAGE_REPORT_INTERVAL_S`). The managed-file path is required; CLI MCP flags conflict with it. CLI version configuration documents the
release pins; Dockerfile ARG defaults retain those pins and npm lock files stay unchanged.

### Permission modes

Teams choose `default`, `acceptEdits`, `plan` or an explicitly authorised bypass mode through
`USER_PERMISSION_MODE`, mapped to each CLI's native policy. VERIFY the mapping on every pinned
release. Bypass is not the organisation default. Managed deny rules, Kata, admission and network
policy apply in every mode.

| Team mode | Claude | Codex | Kimi |
|---|---|---|---|
| default | default | on-request, workspace-write | manual |
| acceptEdits | acceptEdits | on-request, workspace-write | manual (conservative fallback) |
| plan | plan | on-request, read-only | manual, plan enabled |
| bypassPermissions | bypassPermissions | never, danger-full-access | auto |

The requirements allow only the selected Codex approval policy and sandbox modes. Kimi has no
verified accept-edits equivalent, so that team mode retains manual approval. VERIFY native modes and
sandbox support in the target runtime; a sandbox failure requires policy review rather than
automatic escalation.


### Storage decision

`LOGIN_STORAGE=disk` selects an encrypted node disk excluded from all backups. Disk survives pod
restart and suspension. `LOGIN_STORAGE=tmpfs` requires a guest-visible RAM filesystem; node restart
destroys the login and requires a fresh vendor login. `AA_LOGIN_STORAGE` must match the actual
filesystem type, and entrypoints refuse any mismatch. Each directory is owned by the pod UID with
mode 0700. Neither mode permits login copying. Do not treat tmpfs as persistent storage, and do not
describe disk as RAM-only.

## Secrets

The registry pull Secret is required in user namespaces. `session-admission-tls` in the system
namespace holds the admission webhook serving certificate and key; see `secrets.required.yaml`.
Vendor credentials are created by the interactive vendor flow in the login directory, never in
Kubernetes Secrets or environment variables. MCP and pace use projected tokens, not static
credentials.

## Deploy

Render organisation configuration and review manifests before Argo sync. Build images from the
repository root with `docker build -f sessions/<tool>/image/Dockerfile .`; CI pushes digest-pinned
images. Platform administrators provision encrypted node-local directories/PVs, backup exclusions,
namespace labels, policies and registry access. Bootstrap in order: shared system namespace and TLS,
user namespaces, retained login PVs, PVCs and policy, lookup webhook readiness and CA trust,
admission bindings, then StatefulSets. Keep StatefulSets at replicas zero until the positive
admission checks pass. Users cannot create pods or edit policy. The webhook uses only read access to
Namespace/Pod/PVC/PV API objects in the system namespace; session containers never receive that API
identity.

### Vendor-terms gate

Enable only organisation seats approved by the organisation's vendor agreement. VERIFY current
hosting, seat assignment, model availability, Remote Control organisational controls and data
settings with the vendor/admin console before rollout. The source's consumer subscription passages
do not grant an organisation permission. Managed Claude org UUID and exclusive MCP control, and Codex login/workspace keys and native bearer transport are confirmed by the vendor evidence below. Confirm forceLoginMethod spelling with claude doctor on the pinned image. VERIFY Moonshot offers the
required organisation seat arrangement before enabling Kimi. Configure organisational device/session
controls in the vendor admin console. Never route saved logins through a third-party model gateway.

### Login lifecycle

First login occurs interactively inside the holder's pod on its designated home node: `claude auth
login`, `aa-codex login` or the official Kimi login. Start Codex through `aa-codex` so its local
auth lock serializes operations even when pace is unreachable. A Claude listener has capacity one
per pace lease; the account concurrency limit applies across listeners. A different node requires a
new login and seat review; credentials are never migrated. Suspend sets all user StatefulSets to
replicas zero and retains the login claim. Resume restarts on the same node.

Offboard in order: stop workloads, delete the PVC, securely wipe the retained node directory, revoke
the vendor seat/session in the admin console, delete the namespace, and record an identity
tombstone. A Retain PV requires explicit wiping; deleting a namespace alone does not erase
credentials. Audit actions without logging login contents.

## Verify

Run `python -m unittest discover -s sessions/tests -t sessions -v` and offline bash tests. A
platform administrator runs `tests/live/check_admission.py --kubeconfig <file> --rendered <dir>
--user <slug>` and `check_egress.py` only after reviewing target configuration. These scripts are
not run during export. Positive dry-runs in a non-session namespace precede negative tests. Run positive and negative CONNECT dry-runs using the [documented VAP](docs/exec-guard-webhook.md). The moved Markdown guide is an explicit
documentation exception and must never be submitted as a manifest.

### Troubleshooting

A policy hash error requires rerendering and inspecting policy/MCP ConfigMaps; never bypass
validation. Storage errors require checking filesystem type, UID and 0700 permissions on the home
node. Lease denials require waiting for spacing/headroom or closing existing sessions. Unreachable
pace requires checking namespace egress and service readiness. Missing context policy explains
refused snapshots. A Pending pod may indicate its home node or node-affine PV is unavailable; do not
move login data. Authentication failures require the holder to repeat the official flow and
administrators to verify seat/workspace assignment.

## Rollback

Revert rendered policy/image changes and sync Argo after review. Suspend affected StatefulSets
before repair. Preserve retained login directories without copying them; incompatible login storage
changes require fresh login. Rollback never weakens admission, managed denies or egress.

## Security notes

Session root filesystems are read-only and run without privilege, host networking, host paths or
ephemeral containers. API keys, custom vendor base URLs and OAuth tokens in environment variables
refuse startup. Allowed images are digest-pinned. Admission rejects additional workload kinds and
cross-user/tool login mounts. Public 443 egress excludes organisation private/node/API CIDRs; Kimi
uses exact-host CONNECT/SNI egress. Admin break-glass must be time-bounded and audited. Logs and
usage reports omit prompts, payloads and tokens.

Claude managed MCP uses `/etc/claude-code/managed-mcp.json` for exclusive control and loading entitled servers. Since v2.1.259 allowedMcpServers does not constrain these entries; serverName entries are redundant, and deniedMcpServers subtracts. Keep allowManagedMcpServersOnly with an empty allowlist for non-managed paths; prefer serverUrl where applicable. Never pass --mcp-config or --strict-mcp-config with the managed file. API automation uses separate pods/policy without forceLoginOrgUUID or forceLoginMethod. Verify `claude mcp list` contains only managed servers and `claude mcp add` fails with exclusive-control error. Check `claude doctor` on the pinned image: documentation differs between claudeai and claude-ai. Evidence: [managed MCP](https://code.claude.com/docs/en/managed-mcp), [authentication](https://code.claude.com/docs/en/authentication).

Optional user `git` settings name a Secret (`token` key), provider and forge username.
The CLI mounts it read-only at /var/run/agent-array/git-credential; aa-git-credential
responds only to the approved HTTPS provider host and exact registry username. Commit
identity comes from the registry. Provision/revoke the Secret with that user. Other
providers need an approved helper extension. No credential is copied into login homes.

CONNECT uses the namespace-annotation VAP described in
[admission](docs/exec-guard-webhook.md); the TLS lookup webhook now handles storage only.

Deployment verifies official CLI provenance, commercial seat/workspace approval, positive-first admission dry-runs and token rotation. Private homes never move. Preserve executable modes for image helpers; no live login or offboarding wipe is part of offline CI.

Registry pull credentials require per-user sealing: provision the pull Secret in each
USER_NS using a separately reviewed cluster-wide sealing scope, or configure a node-level
registry credential provider and remove pod pull-Secret references in adoption config.
A strict-scope ciphertext sealed for the system namespace cannot decrypt in USER_NS.

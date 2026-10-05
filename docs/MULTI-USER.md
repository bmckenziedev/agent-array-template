# Multi-user operation

Identity authorises access; accounts authorise spending. Teams are entitlement/budget
boundaries, while namespaces isolate individual sessions. Team membership never grants
control of another member's seat. See [org schema](../org/README.md) and [sessions](../sessions/README.md).

## Identity and registries

OIDC `sub` maps to a permanent user slug. Controlled `groups` claims map to teams and
platform-admin/auditor/break-glass roles using configured prefixes. Email is display
metadata, not an identity key. API, portal and GitOps validate issuer, audience, expiry
and application authority independently; proxy authentication is insufficient.

| Authored file | Defines |
|---|---|
| `org/org.yaml` | Deployment, identity, policy, vendors and registry file references |
| `org/teams.yaml` | IdP group, leads, models/budgets, data/vendor permissions, tools/context and fair share |
| `org/users.yaml` | Subject/slug, memberships, primary team, status, tools, home nodes and tombstones |
| `org/accounts.yaml` | Seat holders, API/local pool access, plans/windows/caps and Secret references |
| `org/estates.yaml` | Team-associated repo allowlists, classification, snapshot targets and deny globs |

Example Org's Ana has payments as primary team, namespace `aa-u-ana` and seat
`acct-claude-seat-ana`. `acct-anthropic-api-payments` is separate automation capacity.
Primary team supplies defaults and attribution; explicit task-team selection still
requires membership. Services resolve authority from read-only org-directory, never
from a caller's claimed identity or team alone.

## Namespace and login lifecycle

1. **Onboard:** review IdP membership, never-reused slug, tier and per-person seat binding;
   render namespace, quota, holder RoleBinding and private user/tool/home-node claims.
2. **First login:** holder completes official CLI authentication inside the correct pod;
   verify organisation workspace and managed policy. Do not supply copied auth files.
3. **Home node:** `auto` resolves through eligible configured nodes; review the selected
   placement. Disk homes require encrypted storage/private modes. Moves require stopping
   old listeners, revocation and fresh login, never copying a home.
4. **Suspend:** set `status: suspended`, render zero replicas, stop turns and reconcile
   leases. Retain home according to policy; suspension alone does not revoke IdP access.
5. **Offboard:** revoke IdP/vendor access, stop workloads, revoke task keys/leases, export
   permitted work, delete generated objects and securely erase retained login storage.
   `status: offboarded` omits objects but does not prove retained storage deletion.
6. **Tombstone:** permanently reserve the slug in `tombstones`; never assign it again.

Workspaces are ephemeral. Upload only approved classified data; export before restart or
scale-down. Review patches, run `git apply --check` and local tests before committing or
publishing. Sessions hold no repository publication credential. [CLI transfers](../tools/aa/README.md)
do not bypass estate deny lists or secret scans.

## Login rules R1-R5 and vendor gate

| Rule | Organisation invariant |
|---|---|
| R1 | Official unmodified pinned CLI, vendor login flow, org-workspace restriction; no endpoint/credential overrides |
| R2 | Private login per user/tool/node; never copied, moved, pooled or backed up |
| R3 | Holder starts every seat turn; no queue, cron, webhook or automatic respawn starts it |
| R4 | Per-account concurrency/cap/start spacing through pace; pod ceilings cannot increase allowance |
| R5 | Holder-only access, except separately authorised ticketed audited break-glass |

One seat means one person under an approved organisation contract. Seats are interactive
only. Headless/scheduled automation uses API accounts through the gateway. API accounts
may be team-scoped or explicitly shared; local pools retain attribution and data gates
even without vendor spend. Consumer plans and shared seat logins are excluded by platform
policy. Legal approval must separately confirm commercial terms and data processing.

Never route Claude Code to non-Claude models or extract seat OAuth into LiteLLM, MCP or
another harness. Within-vendor model choice uses official supported controls; cross-vendor
handoff uses reviewed briefs/cards/patches. Verify model names and identity locks against
pinned binaries. Moonshot is disabled by default; Kimi has no MCP configuration in v1.

## Pacing and routing

[Pace](../services/pace/README.md) stores account usage windows and leases on durable
storage, with one replica. Windows represent vendor rolling periods or API budget periods.
`cap_pct` bounds launch headroom; `reserve_pct` records protected allowance. The example
default is 80/20 with account overrides. Confirm window semantics and reserve treatment;
percentages are not vendor cost estimates or quality measures.

Sessions obtain, renew and release leases. TTL is 300 seconds; concurrency, cap and
minimum start spacing are per account. Seat reports require the holder's namespace identity
with pace audience; API/pool reports require authorised platform identities. Manual and
CLI telemetry can be stale: monitor age and reconcile before trusting headroom.

Example `lease_fail_closed: false` can permit interactive startup when pace is unavailable.
High-assurance deployments should set it true and test outage behavior. Vendor limits
remain the ultimate limit. Expiry cleans up abandoned leases; it is not proof an orphaned
tool process stopped.

Automation routes filter to API/pool accounts controlled by or shared with the task team,
permitted for the data class and below cap, then rank by headroom. Seats never route.
Task class cannot override entitlement. API spend telemetry may lag: bound request size
and output, and use vendor-side budgets for hard stops under concurrency.

## Gateway budgets and factory fair share

[LiteLLM](../llm/README.md) syncs teams from registry data. Task keys carry `team_id`,
`user_id`, models intersected with task policy, budget, duration, alias and metadata
`{task_id, account_id, data_class, client}`. Restricted mint clients generate/revoke keys;
the master key never leaves the gateway namespace. Revoke keys at completion/offboarding.
VERIFY edition support for team/budget features before promising enforcement.

[Factory](../modules/factory/README.md) batches carry actor/team attribution and team-lead
approval. Scheduling uses team weights, per-lane inflight limits and priority ceilings,
yielding to interactive demand. Cards need immutable approved context and deterministic
checks. Factory work never drives a seat; results are patches for review, never auto-pushes.

## RBAC, exec guard and break-glass

Holder Role permits own pod inspection/logs, exec/attach, permitted StatefulSet scaling,
selected policy ConfigMap reads and PVC inspection. No Secret reads or pod creation.
Auditors inspect metadata without session control; platform administration is a distinct
authority from seat use.

CONNECT admission allows the holder or break-glass group with ticket annotation.
**VERIFY:** target cluster support and request shape for exec/attach. Until positive and
negative tests pass, use a validated webhook fallback or block access. Cluster-root can
remove the guard; organisational controls and external audit remain necessary.
[Break-glass](runbooks/break-glass.md) is time-limited, not a daily identity.

## Audit attribution

| Who did what | Record |
|---|---|
| Contributor changed registries/policy | Reviewed Git diff and approval |
| Holder exec/attach/scale | API audit metadata: OIDC subject, namespace, verb |
| Admin used break-glass | Ticket, namespace annotation, API audit, expiry/removal |
| Caller invoked MCP | Server resolved actor/team, tool target, allow/deny/error |
| Account usage/lease/route changed | Pace audit and `aa_pace_*` metrics |
| Task minted/spent/revoked key | Mint-client audit and gateway attribution/spend |
| Lead approved/cancelled batch | Factory audit: actor, team and batch |
| Context ingested/exported | Transfer manifest and receiver decision, no source contents |

Each service emits one JSON line with `ts`, `component`, `event`, `actor` (`user`, `sa`,
`sub`), `team`, `target`, `outcome`, `detail`. UTC RFC3339 timestamps and
`allow|deny|error` outcomes are required. Never log prompts, tool payloads, tokens or
file contents. The organisation supplies protected log shipping/retention; stdout is not
an audit archive.

### Control, permission and relay extension contracts

Session kinds determine drivability: platform-spawned sessions have full holder control;
terminal-multiplexer sessions use their recorded handle; headless processes without a
handle support signals only; transcript-only sessions are read-only. Authorise every
command when it runs. Named automation identities never start interactive seats, and
break-glass only lists, reads, interrupts or stops with an audited ticket.

Before leasing, check explicit per-account/per-user refusals and advise on stale usage
or near-cap readings. Permission defaults are automatic reads/workspace writes, policy
engine network checks, and human approval for money, outside contact, irreversible
changes and access changes. The engine may only deny/escalate those four categories
unless a team opts in. Notify once per request and deny on timeout. Bypass permission
modes skip routing; cluster guardrails remain the hard floor.

Redaction covers live output, listings, notifications, policy/adapter payloads, errors,
audit details and logs. Outbound connectors use a dedicated rotating credential per
instance, never a user-token signing key; consoles compare against all configured values
in constant time. Work items link through the CLI session ID, never titles/window names.
Notifier and work-item adapters have bounded failure handling and an explicit interface.
Every control remains available without a console through a holder-only local interface
that the CLI cannot reach. These are extension requirements, not implemented supervision.

Registry git identity controls commit authorship. Forge credentials are per-user named
Secrets, provisioned at onboarding and revoked at offboarding; a home credential grants
no platform authority.

Optional registry example: `git: {credential_secret: forge-user, provider: github, username: forge-login}`.
The Secret stores a `token` key; no credential value enters registries or org-directory.
Only the CLI receives the mount; supervised GIT_* identity env is reserved for that variant.

# Session supervisor

The optional sidecar gives a holder one supervised view of interactive sessions, with private Unix control, redacted output, permission routing and bounded stop operations. Automation may observe or stop; it cannot start work or type into a seat.

## Interface

Use `kubectl exec <pod> -c supervisor -- aa-supervise list`, `tail <id>`, `input <id> <text>`, `interrupt <id> --reason <reason>`, `stop <id> --reason <reason>`, `decide <request> allow`, `link` and `metrics`. Spawn reads its brief from stdin, so it never enters argv; use exec with `-i`. The CLI exits 0 on success, 1 on refusal or transport failure, and 2 on usage errors. See [PROTOCOL.md](PROTOCOL.md) and [supervision](../../docs/SUPERVISION.md). No Service, TCP listener or metrics scrape endpoint exists. The connector is outbound SSE plus JSON POST. Policy comes from supervisor-policy, not org-directory in another namespace.

## Configuration

All settings below are `components.supervisor.<key>` in org.yaml and flatten to `C_SUPERVISOR_<KEY>` (lists use `_JSON`). The image is `C_SUPERVISOR_IMAGE`; build context is repository root, image name is `<IMAGE_REPO_BASE>-supervisor`. `SESSION_LEASE_FAIL_CLOSED` and raw `GROUP_BREAKGLASS` reuse global keys.

| Key | Default |
| --- | --- |
| `enabled` | `true` |
| `image` | `"ghcr.io/example-org/agent-array-supervisor:0.1.0@sha256:0000000000000000000000000000000000000000000000000000000000000000"` |
| `console_url` | `""` |
| `console_cidrs` | `[]` |
| `console_port` | `443` |
| `hook_egress` | `[]` |
| `metrics_push_s` | `60` |
| `automation_identities` | `[]` |
| `leads_can_view` | `false` |
| `drive_discovered` | `true` |
| `spawn_refused_accounts` | `[]` |
| `spawn_refused_users` | `[]` |
| `spawn_advisory_margin_pct` | `10` |
| `spawn_advisory_stale_s` | `3600` |
| `input_max_bytes` | `16384` |
| `input_max_per_minute` | `30` |
| `stops_per_minute_session` | `6` |
| `stops_per_minute_user` | `20` |
| `stop_grace_s` | `10` |
| `permission_tiers` | `{"access-change": "human", "irreversible": "human", "money": "human", "network": "policy", "outside-contact": "human", "read": "auto", "write-workspace": "auto"}` |
| `policy_may_allow` | `[]` |
| `deciders` | `{"access-change": [], "irreversible": [], "money": [], "network": [], "outside-contact": [], "read": [], "write-workspace": []}` |
| `classify_extra` | `[]` |
| `team_policies` | `{}` |
| `policy_hook_url` | `""` |
| `policy_hook_timeout_s` | `10` |
| `human_timeout_s` | `600` |
| `hook_input_max_bytes` | `4096` |
| `notifiers` | `[{"type": "log"}]` |
| `work_item_adapters` | `[]` |
| `redact_extra_patterns` | `[]` |

Enabled chooses the StatefulSet variant. console_url disables the connector when empty; console_cidrs and console_port authorise exact outbound peers. hook_egress lists CIDR/port pairs for relay and policy destinations; receiver authentication remains required because NetworkPolicy applies to the whole pod. metrics_push_s controls pushed frames. automation_identities is an exact allowlist; leads_can_view allows only observation. drive_discovered controls literal typing into recognised tool panes.

Spawn refusal lists run before leases. Advisory margin and stale age report warnings without permitting automation. Gate byte/rate limits apply to brief and input. Stop per-session/per-user caps include interrupts; stop_grace_s bounds termination, with audited emergency bypass. Permission settings configure the category tiers, hook allow exceptions, deciders, additional classifiers, strictest team merge and bounded hook/human timeouts. Notifiers and adapters are credential-free. Extra redaction patterns cannot disable built-in masks.

Missing or malformed policy denies spawn, input and decide. Local exec can still list, output and stop. A holder mismatch refuses startup. Reload occurs when content hash changes.

Principals contain active team leads and category deciders. A separate active-only known_users identity catalog allows ticketed break-glass actors outside the holder's teams to be checked by slug and immutable subject; membership in that catalog grants no ordinary viewing or driving authority.

The private atomic state journal retains spawned handles, lease IDs and sequence state across a sidecar restart. It renews recovered leases immediately and never starts another turn. Only entries recorded in that private journal are recovered from shared volumes, using O_NOFOLLOW and type checks; an unavailable or unverified FIFO downgrades the handle to signal-only. Pending permission connections fail closed on restart.

## Secrets

No Secret reaches a supervised session pod. Audience-specific projected tokens authenticate pace, the connector and hook relays. No upstream credential belongs to a notifier. Login claim roots and home are absent from the sidecar; only read-only transcript subPaths are mounted.

## Deploy

Render organisation configuration and review the selected StatefulSets and per-user ConfigMaps before GitOps sync. Start the connector disabled. Login provisioning must pre-create projects/sessions directories with CLI ownership. Integration Part B must extend admission, managed MCP and image CI before deployment.

Launch is blocked by committed [verified.json](aa_supervisor/verified.json) and the runner's build-time VERIFIED table until pinned CLI/tmux evidence exists. These are reviewed artifact gates, never runtime bypass flags. A platform admin records evidence, updates both gates and rebuilds the images. Permission routing additionally requires the managed aa-permission MCP server and confirmed response shape. Unsupported routing falls back to the team's managed permission mode.

## Verify

Run `python -m unittest discover -s services/supervisor/tests -t services/supervisor` and the sessions suites. Tests use fakes and temporary directories; Unix/FIFO tests skip on Windows. [Deploy-phase checks](../../docs/SUPERVISION.md#deploy-phase-checks) are performed by a platform admin, not offline CI.

VERIFY Claude stream-json input/output flags, verbose, permission-prompt tool mechanism/name/response, interrupt control and init session id; Codex exec --json, stdin prompt, later input and session id; Kimi transcript location; tmux argv without shell, literal keys, socket access and version equality; kubelet subPath ownership; TokenReview pod-name extra; stdio MCP env inheritance; pinned tini TINI_SUBREAPER. VERIFY 08 leaves VM shared tmpfs FIFOs, token rotation and gVisor support unproven. Claude interrupt currently uses C-c; Codex remains signal-only; Kimi spawn is unsupported.

## Rollback

Disable components.supervisor.enabled and render again to select plain StatefulSets. Review and roll pods; preserve login claims. Revert policy through reviewed configuration. No credential backup or migration is involved.

## Security notes

Holder-only spawn, separate PID namespaces, transcript-only mounts, dedicated Memory tmux volume, private control mount, drop ALL, read-only root, no Secret and no listeners are mandatory. Shared files require O_NOFOLLOW and type checks. The sidecar never signals a PID, types into a shell, uses pipe-pane or runs caller shell commands. Every outgoing byte uses Egress, including replies, frames, hooks, plugins, audit and private state files. Prompts and tool payloads never enter audit detail.

Redaction prefix table: sk-/sk-ant-/sk-proj-, gh[pousr]_/github_pat_, glpat-, xox[baprcs]-/xapp-, AKIA/ASIA, ya29./1//, rt_/oai-rt-, Bearer, JWT, signed URL parameters and PEM blocks. URL user:pass credentials and named Secret keys are masked. OAuth access/refresh field names are always masked; arbitrary opaque secrets without a recognised shape or key need extra patterns. Extra patterns extend coverage. Generate committed names with `python services/supervisor/tools/gen_secret_names.py --root .`; freshness is tested.

### Writing a notifier

A module in aa_supervisor/notifiers or aa_supervisor/adapters exposes `Plugin(config, ctx)` and `notify(event)` returning delivered/detail. The context provides audit and post_json only. Example: `ctx.post_json(config["url"], event, 10)` then return delivered true. Plugins must never open sockets or hold credentials. A relay verifies the `<project>-supervisor-hook` token using TokenReview and delivers outside the pod. Delivery uses at most two attempts within one timeout budget. Relays deduplicate request references. Failure is audited and cannot extend a permission deadline.

Classification defaults and the actor matrix are documented in [SUPERVISION.md](../../docs/SUPERVISION.md). Hook allow cannot lower the high-risk category floor without an explicit policy_may_allow entry.

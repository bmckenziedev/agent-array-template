# Session supervision

Session supervision discovers a holder's interactive CLI sessions, exposes redacted output, bounds input and stop requests, and routes permission questions. It does not turn personal seats into a work queue. See [the component](../services/supervisor/README.md) and [the protocol](../services/supervisor/PROTOCOL.md).

## Session kinds and authority

Spawned sessions use a sidecar registry and a CLI-container runner: Claude is full via a FIFO handle, Codex is signal-only via tmux until later-input support is proven. Discovered recognised tool panes are full via literal tmux keys when drive_discovered is enabled; Remote Control listeners remain signal-only. Shell panes are excluded and rechecked immediately before input. Headless transcripts have read-only output and recency state, no drive or stop handle: separate PID namespaces prevent signalling their processes.

| Command | Holder | Ticketed break-glass | Listed automation | Team lead with leads_can_view |
| --- | --- | --- | --- | --- |
| list/output | yes | yes | yes | yes |
| spawn | yes | seat_interactive_only | seat_interactive_only | seat_interactive_only |
| input/link | yes | no | no | no |
| interrupt | yes | yes | no | no |
| stop | yes | yes | yes | no |
| permission.decide | yes | no | no | only listed category decider |

Unknown, suspended and offboarded actors are denied before other checks. Holder identity is OIDC-authenticated by the console and re-authorised against current policy. Local `kubectl exec -c supervisor -- aa-supervise` is holder-equivalent because the API server and exec guard admit the holder and ticketed break-glass and audit the exec. A break-glass local exec therefore has holder-equivalent capability, matching existing CLI exec exposure. A pod without AA_USER has null ownership and allows only known ticketed break-glass stop/interrupt.

## Permission routing

| Category | Tool/argument patterns | Default tier |
| --- | --- | --- |
| read | read, cat, list, grep, search | auto |
| write-workspace | write, edit, patch, mkdir | auto |
| network | fetch, curl, http, network, download | policy |
| outside-contact | send, publish, message, contact | human |
| irreversible | delete, destroy, rm, drop, force | human |
| money | pay, purchase, charge, billing, money | human |
| access-change | grant, revoke, chmod, permission, credential, access | human |

Highest risk wins: access-change, money, irreversible, outside-contact, network, write-workspace, read. classify_extra extends the table. Unknown requests use policy. Auto allows and records; policy uses conservative built-in rules then an optional redacted bounded hook, whose timeout escalates. Filtered high-risk categories can be denied or escalated by a hook; allow requires policy_may_allow. Human sends one notification per request id; holder or a listed active decider answers, and timeout denies.

Each user's team overrides are merged by strictest tier (human > policy > auto), intersection of policy_may_allow and category deciders, and minimum timeout. Empty deciders means holder only. The plugin emits supervisor-policy in the user's namespace with active principals. Bypass modes produce no permission tool calls; managed deny rules, admission, egress and exec guard remain the hard floor. Discovered TUI sessions answer prompts at their terminal. Routing is limited to verified spawned Claude sessions.

## Output, gates and pacing

One Egress wrapper redacts replies, frames, notifications, hook summaries, adapter payloads, state files, audit and logs before export. It masks token prefixes, OAuth/JWT/Bearer shapes, PEM private keys, credentialed URLs, declared Secret key values and extra patterns. Opaque unknown secrets remain a residual risk; no runtime flag disables masking. Output comes from stream-json files, capture-pane polling or transcript tails, never pipe-pane.

The input gate enforces UTF-8, byte and minute caps, sender authority and rejection of control characters except newline/tab. Rejection bursts notify. Stop plus interrupt caps apply per session and user; non-empty reason is required. Emergency bypass is audited and notifies. Stops use C-c then kill-pane after grace; only aa-spawn-run signals its own child and escalates TERM to KILL. TINI_SUBREAPER is set without relying on PID 1.

Spawn checks holder authority, refusal lists, pod cap, pace advisory, lease and launch in that order. Refusal happens before any lease. Near-cap and stale readings warn without granting automation authority. The sidecar acquires, renews every 60 seconds and releases its spawned leases; discovered wrappers retain their own leases. Unreachable pace can fail open with an advisory, or fail closed through SESSION_LEASE_FAIL_CLOSED. Authentication refusals never fail open. Launch remains blocked until pinned CLI/tmux VERIFY evidence is recorded in reviewed image artifacts.

Git identity comes only from registry env: slug name, USER_EMAIL email, USER_GITHUB login, source registry. Credential remains unknown; the sidecar never reads home git config or credential helpers. Work-item joins use CLI session ids, never titles, windows or briefs.

## Console and plugins

An empty console_url disables the connector. Otherwise outbound HTTPS SSE receives commands and JSON POST emits hello, responses, output and metrics. The receiver verifies the projected `<project>-supervisor` token, namespace labels and pod binding with [console_ref/verify.py](../services/supervisor/console_ref/verify.py). Tokens are re-read per request and after 401. The console is trusted to authenticate human actors; the supervisor re-authorises every command. There is no shared connector secret, port-forward, inbound Service or metrics listener.

Notifier and work-item adapter modules expose Plugin(config, ctx). Their context offers only audit and redacted bounded TLS post_json. Credentialed delivery uses an external relay that TokenReviews the distinct supervisor-hook token. The policy hook shares this path. Delivery failures do not extend human timeouts.

console_cidrs/console_port and hook_egress configure exact per-user egress. Unrestricted CIDRs and API-server IP coverage are rejected. NetworkPolicy is per pod, so the CLI can also reach allowed peers: every receiver must authenticate every request. Organisations needing hostname-level control can use an authenticated egress gateway with exact upstream allowlists. Pace egress is already supplied by session infrastructure.

## Residual risks

VERIFY 08 establishes the same UID needed for tmux's 0700 socket directory. Isolation rests on separate PID namespaces and container mounts. The login root, home, private supervisor state and supervisor tokens are not exposed to peer containers. Same-holder sessions already share a UID and can see each other's panes and shared spawn I/O. Fixed tmux commands can execute as the CLI user; their reviewed argv set is the boundary. Node-root and cluster-root remain trusted. Console actor assertions and local exec identity are trusted boundaries.

## Deploy-phase checks

A platform admin validates under the VM runtime class: same-UID tmux access through aa-tmux; Unix sockets and FIFOs on shared medium: Memory emptyDirs; C-c and kill-pane; projected-token rotation through the VM shared filesystem; read-only transcript subPath readability with CLI write access; and CLI inability to see supervisor-run or supervisor tokens. VERIFY 08 backs the socket design from source, while FIFO behavior and token rotation remain open. gVisor behavior is unproven and unsupported.

Confirm pinned Claude flags, verbose, permission tool name/mechanism/response, interrupt control and init id; Codex JSON/stdin/later-input/id; Kimi transcript path; tmux multi-argument launch without shell, send-keys -l, -S cross-container access and client/server version match; subPath directory ownership; TokenReview pod-name extra; stdio MCP AA_SUPERVISOR_SESSION inheritance; and pinned tini TINI_SUBREAPER. Offline tests do not clear these markers.

# Supervision protocol, version 1

One bounded UTF-8 JSON object per line, or per SSE `data:` event. Maximum frame size is 65536 bytes. Commands carry `protocol_version: 1`, `command`, `actor {kind: user|automation|local, id, sub, groups}` and optional `ticket`. Control-socket identity is local holder-equivalent; connector actors are authenticated by the console and re-authorised for every command.

| Command | Fields |
| --- | --- |
| `hello` | protocol_version, pod, user, tool, account, supervisor_version, resume_seq |
| `sessions.list` | returns sessions |
| `sessions.spawn` | tool, brief, cwd, estate_id, work_item |
| `sessions.input` | id, text |
| `sessions.interrupt` | id, reason |
| `sessions.stop` | id, reason, emergency |
| `sessions.link` | id, session_id, work_item |
| `sessions.output` | id; returns id, seq, kind, redacted, payload |
| `permission.request` | request_id, session_id, tool, arguments, summary; permission socket only |
| `permission.decide` | request_id, decision: allow or deny, by |
| `metrics` | returns counters; no listener |

Output kinds are text, tool, permission, advisory and exit. Every output has `redacted: true`. Work items have system, id and HTTPS url. Session records carry id, kind, tool, owner, team, account, session_id, work_item, started_at, cwd, window, pane, last_activity, state, drivable, drive_via, permission_mode, permission_routing and registry git_identity. Headless records have no pane or drive handle.

Stable errors: forbidden, not_found, not_drivable, gate_rejected, rate_limited, account_refused, seat_interactive_only, lease_denied, pace_unreachable, unsupported_version, unsupported_tool, policy_unavailable. Unverified launch returns not_drivable with a VERIFY detail. Permission failures deny.

## Console authentication

The console uses TokenReview with audience `<project>-supervisor`, namespace lookup and [the reference verifier](console_ref/verify.py). It accepts only an authenticated `system:serviceaccount:<user namespace>:session` token, the user namespace prefix and kind label, matching namespace user and hello.user, and the bound pod-name extra matching hello.pod. Hook tokens and API tokens are rejected. VERIFY the pod-name extra on the target Kubernetes version.

Every GET and POST carries the projected connector token, read again per request and after a 401. No shared secret exists. Commands arrive through outbound `GET <console_url>/v1/supervisor/commands`; replies and frames use outbound `POST <console_url>/v1/supervisor/events`. The console is trusted to authenticate forwarded human actors. The supervisor checks current policy again for each command. The stream reconnects within 50 minutes, uses capped jittered backoff and resumes with Last-Event-ID and resume_seq. POST acknowledgement is ack_seq.

The private control socket is `/run/aa-supervisor/control.sock`; only the supervisor mounts that emptyDir. The CLI sees `/run/aa/permission.sock`, which accepts permission.request only. Neither channel binds TCP/UDP or supports port-forward.

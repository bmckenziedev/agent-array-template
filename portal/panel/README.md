# Identity-aware panel

The panel maps verified OIDC subjects to the org directory and offers bounded upload, optional task dispatch and bundle retrieval.

## Interface

The public listener on 8080 exposes `/api/me` (slug, teams, roles), `/api/tasks`, task status/events/bundle and cancellation.
`POST /api/tasks/{id}/approve` records a team lead or platform admin approval for a `needs-review` bundle.
Members see and cancel their own tasks; leads see and cancel their teams' tasks; platform admins access all tasks;
auditors read all tasks and cannot mutate them. Tasks retain the submitting user slug, team, account and data class.
The internal listener on 8081 accepts only per-task callback tokens. The local socket selects the listener independently of headers.
Every task API returns 501 with an explicit message when session-jobs is disabled.

Task creation is multipart: brief, files or archive, optional team, account_id, data_class, mode, base_commit, repos and test_cmd.
The primary team is the default; it must be present in both directory memberships and verified token groups.
Only entitled API or pool accounts with an allowed vendor/data class can be selected; seats cannot run tasks.

## Configuration

`components.panel` defaults are `max_active_tasks_per_user: 3`, `max_active_tasks_per_team: 10`,
`task_models: [local-coder, local-coder-small]`, and `task_duration_seconds: 3900`.
The corresponding `C_PANEL_*` values configure the rendered deployment. Limits are checked under the creation lock.
Team `litellm.task_budget_usd` sets each mode's budget; models intersect team, account and component allowlists.
`ACCESS_KIND=oidc-proxy` validates bearer ID/access tokens or the proxy's forwarded signed token against
`OIDC_ISSUER_URL` and `OIDC_CLIENT_ID`. Discovery validates the issuer and obtains HTTPS JWKS.
`cloudflare-access` validates `Cf-Access-Jwt-Assertion` against `CF_ACCESS_TEAM` and the `CF_ACCESS_AUD` Secret value.
Both modes require a stable `sub` that matches `users.json`; Cloudflare subject mapping must be provisioned in the directory.
JWKS cache lifetime is one hour; unknown kids trigger a refresh no more often than every ten seconds.
A freshly rotated key may be refused during that short refresh interval. Stale keys expire even if refresh fails.
`OIDC_GROUPS_CLAIM`, `GROUP_PLATFORM_ADMIN` and `GROUP_AUDITOR` configure claims and privileged groups.
`ORG_DIRECTORY` defaults to `/etc/agent-array/org`. Unknown, duplicate, suspended and offboarded subjects fail closed.
`SESSION_JOBS_ENABLED` defaults false. Runtime defaults Kata; gVisor needs the seccomp-gvisor module.

Uploads default to 25 MiB, 5000 files, 128 MiB expansion and a 100:1 expansion bound; bundle uploads are 64 MiB.
The panel removes input after fetching, revokes local tokens and model keys on termination, and retries remote revocation.
Bundles expire one hour after first download or 24 hours after termination. Tombstones retain authorization metadata for 30 days.

## Secrets

See [secret inventory](secrets.required.yaml). `litellm-mint` contains a route-restricted mint key, never a master key.
LiteLLM must authorize this credential to mint across entitled org users/teams while limiting routes to
`/key/generate`, `/key/delete`, `/key/info`; team budgets cap aggregate spending.
The panel checks returned key scope, budget, expiry and metadata before dispatching.

## Deploy

Build `docker build -f portal/panel/Dockerfile .`, pin the resulting image in org configuration, render and sync portal manifests.
Platform hardening creates the portal namespace; the directory producer supplies `org-directory` in that namespace.
Session-jobs supplies the mounted template and its Jobs-only cross-namespace Role when enabled.

## Verify

Install the hash-locked requirements in a temporary environment, then run
`python -B -m unittest discover -s portal/panel/tests -t portal/panel`.
The separate [Docker harness](../e2e/README.md) is optional and never part of this unit suite.
Live verification should confirm OIDC discovery, group claims, directory subject mapping and mint-key permissions.

## Rollback

Restore the previous digest and configuration. Disable session-jobs to stop task APIs; terminate existing Jobs and revoke outstanding keys.
Keep the PVC until retention and revocation have completed.

## Security notes

Preserved controls include same-origin mutations, body limits before parsing, strict archive members, bounded SSE,
one PVC writer, terminal token revocation, key expiry and socket-separated routes. Audit JSON lines exclude tokens, prompts and files.
Jobs carry short-lived task credentials only in the runner environment because the panel Role grants no Secret API rights.
Anyone able to read these Jobs can read their credentials; restrict namespace access and use short TTLs.
NetworkPolicies admit the access proxy only to 8080 and session Jobs only to 8081.

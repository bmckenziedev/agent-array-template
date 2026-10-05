# Module: hermes-ops-chat (optional)
Optional read-only ops chat in NS_OPS with org API-account model calls through LiteLLM, identity gating and isolated user memory.

## Interface
Telegram private messages or Slack Socket Mode direct messages. Secret identities maps platform ids to active org-directory slugs; allowed-users.json is a JSON list of platform-id strings. Membership must match the configured team in users.json; chat identities have no IdP group membership. Only cluster_status, prom_query and alerts are exposed by the /opt/arrayops stdio MCP server with readOnlyHint=true.

## Configuration
Global namespace, identity, runtime, image and cluster network keys follow the organisation configuration.

- `enabled` â†’ `M_HERMES_OPS_CHAT_ENABLED`, default `false`.
- `platform` â†’ `M_HERMES_OPS_CHAT_PLATFORM`, default `telegram`.
- `bot_secret` â†’ `M_HERMES_OPS_CHAT_BOT_SECRET`, default `hermes-chat-bot`.
- `identity_secret` â†’ `M_HERMES_OPS_CHAT_IDENTITY_SECRET`, default `hermes-allowed-users`.
- `allowed_team` â†’ `M_HERMES_OPS_CHAT_ALLOWED_TEAM`, default `platform`.
- `model` â†’ `M_HERMES_OPS_CHAT_MODEL`, default `openai-api-standard`.

## Secrets
Names and key names only; see [secrets.required.yaml](secrets.required.yaml). No values are committed.
For configurable bot/identity Secret names, provisioning must use the configured names rather than the defaults.

## Deploy
Enable the module; render and build the pinned image from the repository root. Provision bot Secrets and a scoped hermes-llm-key virtual key backed by an org API account. Slack additionally requires app-token (connections:write) and a bot with im:history/chat:write, with message.im subscriptions enabled. Deploy home PVC, read RBAC and policies. Run documented admission dry-runs before binding the Deny policy.

## Verify
Run `python -m unittest discover -s modules/hermes-ops-chat/tests -t modules/hermes-ops-chat` offline.
Render tests use an unmodified canonical fixture; modules are enabled only in an in-memory copy.
Live checks require a platform admin and are never part of offline tests.

## Rollback
Scale the Deployment to zero, revoke the LiteLLM and bot keys, then remove bindings. Preserve or securely delete per-user memory according to org retention policy.

## Security notes
No consumer OAuth authentication exists in the bridge. Model calls have user=directory slug. Credentials are mounted only as named Secrets. Data visible: cluster object metadata/specs/status (including pod arguments and literal environment values), configured metrics/alerts and the current user conversation memory. Pod logs, Secrets and ConfigMaps are not granted in cluster RBAC. Tool data is untrusted; the model has no terminal/file/browser tool. Memory is local per-user chat history and may contain organisational data: exclude the home from broad backups. The pinned upstream source is retained in /opt/hermes for review; the hardened gateway uses a restricted LiteLLM tool loop rather than enabling the upstream general-purpose gateway.

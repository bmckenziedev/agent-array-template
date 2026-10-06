# LLM gateway

LiteLLM routes organisation API accounts and optional local GPU pools. Teams own budgets and model allowlists; short-lived task keys attribute spend to both the submitting user and team. Subscription seats remain interactive vendor sessions.

Decision record: [0016: Vendor seat and API account model](../docs/adr/0016-vendor-seat-and-api-account-model.md).

## Interface

ClusterIP `litellm` exposes the gateway on port 4000 in `NS_LLM`. `litellm-pg:5432` stores keys and spend, and `litellm-redis:6379` coordinates counters. The stdlib render plugin emits a ConfigMap, the complete Deployment, `files/llm/provider-env.json` and `files/llm/teams.json`. JSON is valid YAML; Kubernetes templates use JSON syntax to preserve types.

`teams_sync.py --base-url <gateway> [--file rendered/files/llm/teams.json] plan` prints reconciliation actions without mutation. `plan --check-secrets` lists required API account Secret names and keys; it does not inspect Kubernetes. `apply --yes` reconciles managed users, teams and memberships. Stale team/user deletion additionally requires `--allow-delete`; unmanaged objects are preserved and identity collisions fail. Membership removals apply immediately because retaining access after removal is unsafe. User creation disables automatic key generation. Commands exit 0 on success and 1 on validation or API failure.

## Configuration

`components.llm.models.<group>` maps account `litellm_models` to upstream IDs. Defaults are `anthropic/claude-sonnet-4-5-20250929` and `openai/gpt-4.1`, verified against the official [Anthropic provider documentation](https://docs.litellm.ai/docs/providers/anthropic) and [OpenAI provider documentation](https://docs.litellm.ai/docs/providers/openai). Confirm account entitlement and model lifecycle before deployment. Additional API groups require an explicit vendor-prefixed setting.

`components.llm.fallbacks` defaults to `local-coder: [local-coder-small]`. Only present groups participate. API-to-pool and pool-to-API edges are refused, including local-to-paid escalation. This export conservatively refuses cross-type fallbacks even when a team would allow paid escalation. Fallback destinations must also be on the task key because `enforce_fallback_model_access` is enabled. Account type, team budgets, duration, tpm/rpm, task budgets, and active members come from the normalised model. Seat accounts never produce groups or Secret references. GPU groups appear only when `modules.gpu-lanes.enabled` is true and lanes reference pool groups.

Global keys consumed: `NS_LLM`, `NS_MODELS`, `NS_PORTAL`, `NS_SYSTEM`, `NS_FACTORY`, `NS_SESSION_JOBS`, `NS_OPS`, `LABEL_PREFIX`, `PROJECT_NAME`, `STORAGE_CLASS_DEFAULT`, `CLUSTER_DNS_IP`, `PRIVATE_CIDRS_JSON`. Provider variables are named `AA_PROVIDER_<UPPER_UNDERSCORED_ACCOUNT_ID>`.

## Secrets

See [secrets.required.yaml](secrets.required.yaml). API account credentials must be Secrets in `NS_LLM`, referenced individually only by LiteLLM. `LITELLM_MASTER_KEY` is read from the environment by the admin CLI, never argv. Run administration inside the LLM admin boundary; no desktop client fetches the master key. Preserve `LITELLM_SALT_KEY` across restarts and restores. Postgres fields must match `DATABASE_URL`. Generate a nonempty URL-safe Redis password (`A-Z`, `a-z`, digits, `_`, `-`); the entrypoint rejects config-injection characters before creating its private temporary config.

Portal and farm-mcp receive distinct `litellm-mint:LITELLM_MINT_KEY` Secrets in their namespaces. Run `mint-key-for panel` (`portal` is also accepted) or `mint-key-for farm-mcp` with `--seal-command <program> <arguments...>`. The command must accept the plaintext key on stdin and produce only a sealed artifact in its own authorised output path. An adapter around `platform/sealed-secrets/seal.sh --namespace-ref portal --name litellm-mint --keys LITELLM_MINT_KEY` is required if that script does not accept raw stdin. The key passes directly to the sealing subprocess, never stdout, stderr, argv or a plaintext file. Failed sealing revokes the minted key. The CLI prints only sealing instructions. This interface intentionally requires a working sealer before minting.

Mint service identities use route-restricted management keys for `/key/generate`, `/key/delete`, `/key/info`. They are trusted admin services: route restrictions alone do not constrain the body of generated keys. Portal/farm must validate user membership, team and data-class entitlements, intersect task models with team models, cap task budgets, and refuse requests that bypass attribution. Always send `team_id`, `user_id` (slug), `models`, `max_budget`, `duration`, `key_alias: <client>-<task id>` and `metadata: {task_id, account_id, data_class, client}`. Delete task keys on terminal states. End users never receive mint keys.

## Deploy

Render through `python tools/render/render.py --org org/org.yaml --out rendered --strict`. Argo consumes `rendered/global/llm/k8s`. Seal dependencies before syncing and reconcile users/teams before enabling mint clients. The plugin mounts `litellm-admin` read-only at `/opt/llm-admin` with the CLI and desired directory. Platform admins can exec `python /opt/llm-admin/teams_sync.py --base-url http://127.0.0.1:4000 --file /opt/llm-admin/teams.json plan` inside the LiteLLM container, then run `apply --yes` after reviewing the plan. This uses the container's master-key environment and localhost without exporting the master key or widening ingress. The platform hardening component owns the Namespace and defaults `NS_LLM` to baseline with restricted warn/audit. This component creates only objects inside it.

All three workload templates satisfy restricted PSA: explicit nonroot IDs, RuntimeDefault seccomp, no privilege escalation, dropped capabilities, no host mounts and no service account token. LiteLLM's pinned database image needs a writable root filesystem to generate Prisma clients; restricted PSA permits that exception. Postgres runs as Alpine UID 70 with writable data and socket directories; Redis uses UID 999 with writable data and `/tmp`. Runtime startup under these IDs remains a required live check. No live restricted-admission claim is made.

Keep one LiteLLM replica and one worker. The source router can fall back to local counters if Redis fails; monitor readiness and fail operational traffic closed when coordination is unavailable. Back up Postgres and Redis PVCs with the platform backup task. No gateway pod needs Kubernetes API egress; admin reconciliation uses HTTP, so no apiserver rule is added.

## Verify

`python -m unittest discover -s llm/tests -t llm -v` exercises fixture rendering, template parsing, account Secret mapping and an in-process fake admin API. The default OSS tier uses member role user; enterprise_license explicitly enables admin membership and the optional licence Secret key. The pinned [request schemas](https://github.com/BerriAI/litellm/blob/v1.104.0/litellm/proxy/_types.py) require separate membership endpoints; the synchroniser uses them. Live checks must prove cross-team denial, spend limits, member removal, fallback access denial, mint key route denial and no prompt/response logs. Validate network denial for wrong namespaces and missing client labels.

## Rollback

Restore the previous rendered manifests and database backup through the owning Argo application. Review plan before applying an older team model; budget and membership changes may revoke access. Rotate compromised mint keys and delete their task keys; retain the salt.

## Security notes

No subscription credentials in the gateway. No routing Claude Code to non-Claude models. Prompt logging is disabled by both message and spend-log settings. Public HTTPS egress permits provider calls while excluding configured private CIDRs; Kubernetes L3/L4 policies cannot restrict provider domains or distinguish HTTPS payloads. Services remain ClusterIP and ingress requires both one of the five approved namespaces and the `LABEL_PREFIX/llm-client: "true"` pod label. Only LiteLLM reaches Postgres and Redis.

OSS mode uses member role user. `components.llm.enterprise_license` defaults false; when enabled it permits team admins and reads optional LITELLM_LICENSE from litellm-env. Mint-key owners are proxy_admin users protected only by the exact allowed_routes `/key/generate`, `/key/delete`, `/key/info`; key_type is never set. Prometheus callbacks and budget initialization are enabled; metrics run on 4001 with monitoring-only ingress. Evidence: [LiteLLM Prometheus](https://docs.litellm.ai/docs/proxy/prometheus).

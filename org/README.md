# Org configuration

One reviewed configuration surface defines the platform directory, infrastructure settings and policy.
The renderer consumes this data and component defaults; secrets appear only as references by name and key.

## Interface

Copy each `org/*.example.yaml` to the corresponding file without `.example`, copy the MCP example files,
and update `org.files` in `org/org.yaml` to those repository-relative paths. The canonical example points
at example files so it can be rendered independently. JSON Schema 2020-12 editor schemas live in
`schema/`; cross-file relationships are checked by the renderer, not JSON Schema.

## Configuration

The tables enumerate every field. `[]` means each list item. Catalog names (tiers, labels, module settings,
component settings, task-budget classes) are organisation-defined. A required field has no implicit default.
Defaults are exactly those applied by the normaliser; example values are examples, not hidden defaults.
All documents require integer `version: 1`. Container rows describe grouped settings; child rows describe
individual settings. Placeholder names in tables are bare names, not template tokens.

### org.yaml fields

| Field | Type | Default | Allowed values / validation | Placeholder keys produced |
| --- | --- | --- | --- | --- |
| `version` | integer | required | constant 1 | Model/directory only; no direct placeholder |
| `project` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `project.name` | string | required | Type; cross-file rules below | PROJECT_NAME |
| `project.label_prefix` | string | required | Type; cross-file rules below | LABEL_PREFIX |
| `project.image_prefix` | string | required | Type; cross-file rules below | IMAGE_PREFIX |
| `org` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `org.name` | string | required | Type; cross-file rules below | ORG_NAME |
| `org.domain` | string | required | Type; cross-file rules below | ORG_DOMAIN |
| `org.timezone` | string | required | Type; cross-file rules below | ORG_TIMEZONE |
| `org.security_contact` | string | required | Type; cross-file rules below | SECURITY_CONTACT |
| `github` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `github.org` | string | required | Type; cross-file rules below | ORG_GITHUB_ORG |
| `github.repo` | string | required | Type; cross-file rules below | ORG_GITHUB_REPO |
| `github.git_remote` | string | required | Type; cross-file rules below | ORG_GIT_REMOTE |
| `github.revision` | string | required | Type; cross-file rules below | GIT_REVISION |
| `github.platform_team_handle` | string | required | Type; cross-file rules below | CODEOWNERS_PLATFORM |
| `registry` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `registry.host` | string | required | Type; cross-file rules below | REGISTRY_HOST |
| `registry.namespace` | string | required | Type; cross-file rules below | REGISTRY_NAMESPACE |
| `registry.pull_secret` | string | required | Type; cross-file rules below | REGISTRY_PULL_SECRET |
| `images` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `images.session-claude` | object | required | Type; cross-file rules below | IMAGE_SESSION_CLAUDE |
| `images.session-claude.tag` | string | required | Type; cross-file rules below | IMAGE_SESSION_CLAUDE |
| `images.session-claude.digest` | string | required | pattern ^sha256:[0-9a-f]{64}$ | IMAGE_SESSION_CLAUDE |
| `images.session-codex` | object | required | Type; cross-file rules below | IMAGE_SESSION_CODEX |
| `images.session-codex.tag` | string | required | Type; cross-file rules below | IMAGE_SESSION_CODEX |
| `images.session-codex.digest` | string | required | pattern ^sha256:[0-9a-f]{64}$ | IMAGE_SESSION_CODEX |
| `images.session-kimi` | object | required | Type; cross-file rules below | IMAGE_SESSION_KIMI |
| `images.session-kimi.tag` | string | required | Type; cross-file rules below | IMAGE_SESSION_KIMI |
| `images.session-kimi.digest` | string | required | pattern ^sha256:[0-9a-f]{64}$ | IMAGE_SESSION_KIMI |
| `images.panel` | object | required | Type; cross-file rules below | IMAGE_PANEL |
| `images.panel.tag` | string | required | Type; cross-file rules below | IMAGE_PANEL |
| `images.panel.digest` | string | required | pattern ^sha256:[0-9a-f]{64}$ | IMAGE_PANEL |
| `images.session-runner` | object | required | Type; cross-file rules below | IMAGE_SESSION_RUNNER |
| `images.session-runner.tag` | string | required | Type; cross-file rules below | IMAGE_SESSION_RUNNER |
| `images.session-runner.digest` | string | required | pattern ^sha256:[0-9a-f]{64}$ | IMAGE_SESSION_RUNNER |
| `images.pace` | object | required | Type; cross-file rules below | IMAGE_PACE |
| `images.pace.tag` | string | required | Type; cross-file rules below | IMAGE_PACE |
| `images.pace.digest` | string | required | pattern ^sha256:[0-9a-f]{64}$ | IMAGE_PACE |
| `images.farm-mcp` | object | required | Type; cross-file rules below | IMAGE_FARM_MCP |
| `images.farm-mcp.tag` | string | required | Type; cross-file rules below | IMAGE_FARM_MCP |
| `images.farm-mcp.digest` | string | required | pattern ^sha256:[0-9a-f]{64}$ | IMAGE_FARM_MCP |
| `images.factory` | object | required | Type; cross-file rules below | IMAGE_FACTORY |
| `images.factory.tag` | string | required | Type; cross-file rules below | IMAGE_FACTORY |
| `images.factory.digest` | string | required | pattern ^sha256:[0-9a-f]{64}$ | IMAGE_FACTORY |
| `images.hermes-chat` | object | required | Type; cross-file rules below | IMAGE_HERMES_CHAT |
| `images.hermes-chat.tag` | string | required | Type; cross-file rules below | IMAGE_HERMES_CHAT |
| `images.hermes-chat.digest` | string | required | pattern ^sha256:[0-9a-f]{64}$ | IMAGE_HERMES_CHAT |
| `cluster` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `cluster.distribution` | string | required | Type; cross-file rules below | CLUSTER_DISTRIBUTION |
| `cluster.version` | string | required | Type; cross-file rules below | K8S_VERSION |
| `cluster.provider` | string | required | Type; cross-file rules below | HOSTING_PROVIDER |
| `cluster.cni` | string | required | Type; cross-file rules below | CNI |
| `cluster.apiserver` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `cluster.apiserver.endpoint_ips` | array | required | Type; cross-file rules below | APISERVER_ENDPOINT_IPS_JSON,APISERVER_ENDPOINT_IP |
| `cluster.apiserver.port` | integer | required | Type; cross-file rules below | APISERVER_PORT |
| `cluster.apiserver.service_ip` | string | required | Type; cross-file rules below | APISERVER_SERVICE_IP |
| `cluster.apiserver.url` | string | required | Type; cross-file rules below | APISERVER_URL |
| `cluster.cidrs` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `cluster.cidrs.pod` | string | required | Type; cross-file rules below | POD_CIDR |
| `cluster.cidrs.service` | string | required | Type; cross-file rules below | SERVICE_CIDR |
| `cluster.cidrs.overlay` | string | required | Type; cross-file rules below | OVERLAY_CIDR |
| `cluster.cidrs.private` | array | required | Type; cross-file rules below | PRIVATE_CIDRS_JSON |
| `cluster.cidrs.session_egress_deny_extra` | array | required | Type; cross-file rules below | SESSION_EGRESS_DENY_CIDRS_JSON |
| `cluster.dns_service_ip` | string | required | Type; cross-file rules below | CLUSTER_DNS_IP |
| `cluster.storage` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `cluster.storage.default_class` | string | required | Type; cross-file rules below | STORAGE_CLASS_DEFAULT |
| `cluster.storage.login_class` | string | required | Type; cross-file rules below | STORAGE_CLASS_LOGIN |
| `cluster.storage.login_host_root` | string | required | Type; cross-file rules below | LOGIN_HOST_ROOT |
| `cluster.storage.login_storage` | string | required | Type; cross-file rules below | LOGIN_STORAGE |
| `cluster.runtime_classes` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `cluster.runtime_classes.vm` | string | required | Type; cross-file rules below | RUNTIME_CLASS_VM |
| `cluster.runtime_classes.gvisor` | string | required | Type; cross-file rules below | RUNTIME_CLASS_GVISOR |
| `cluster.runtime_classes.gpu` | string | required | Type; cross-file rules below | RUNTIME_CLASS_GPU |
| `namespaces` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `namespaces.system` | string | required | Type; cross-file rules below | NS_SYSTEM |
| `namespaces.llm` | string | required | Type; cross-file rules below | NS_LLM |
| `namespaces.mcp` | string | required | Type; cross-file rules below | NS_MCP |
| `namespaces.portal` | string | required | Type; cross-file rules below | NS_PORTAL |
| `namespaces.factory` | string | required | Type; cross-file rules below | NS_FACTORY |
| `namespaces.models` | string | required | Type; cross-file rules below | NS_MODELS |
| `namespaces.egress` | string | required | Type; cross-file rules below | NS_EGRESS |
| `namespaces.session_jobs` | string | required | Type; cross-file rules below | NS_SESSION_JOBS |
| `namespaces.ops` | string | required | Type; cross-file rules below | NS_OPS |
| `namespaces.monitoring` | string | required | Type; cross-file rules below | NS_MONITORING |
| `namespaces.argocd` | string | required | Type; cross-file rules below | NS_ARGOCD |
| `namespaces.user_prefix` | string | required | Type; cross-file rules below | USER_NS_PREFIX,USER_NS |
| `nodes` | array | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `nodes.[].name` | string | required | Type; cross-file rules below | NODE_NAME,ENTITY_ID |
| `nodes.[].short` | string | required | pattern ^[a-z][a-z0-9-]{0,14}$ | NODE_SHORT |
| `nodes.[].roles` | array | required | Type; cross-file rules below | NODE_ROLES_JSON |
| `nodes.[].overlay_ip` | string | required | Type; cross-file rules below | NODE_OVERLAY_IP |
| `nodes.[].public_ip` | string / null | null | Type; cross-file rules below | NODE_PUBLIC_IP,NODE_PUBLIC_IPS_JSON |
| `nodes.[].runtime_classes` | array | [] | Type; cross-file rules below | NODE_RUNTIME_CLASSES_JSON |
| `nodes.[].lan_ip` | null / string | null | Type; cross-file rules below | NODE_LAN_IP |
| `nodes.[].gpu` | object / null | null | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `nodes.[].gpu.model` | string | required | Type; cross-file rules below | NODE_GPU_MODEL |
| `nodes.[].gpu.vram_gb` | integer | required | Type; cross-file rules below | NODE_GPU_VRAM_GB |
| `nodes.[].gpu.count` | integer | required | Type; cross-file rules below | NODE_GPU_COUNT |
| `nodes.[].labels` | object | {} | Type; cross-file rules below | NODE_LABELS_JSON |
| `network` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `network.overlay` | string | required | Type; cross-file rules below | OVERLAY_KIND |
| `network.tailscale` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `network.tailscale.tailnet` | string | required | Type; cross-file rules below | TAILNET_NAME |
| `network.tailscale.node_tag` | string | required | Type; cross-file rules below | TS_TAG_NODES |
| `network.access` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `network.access.kind` | string | required | Type; cross-file rules below | ACCESS_KIND |
| `network.access.cloudflare_team` | string | required | Type; cross-file rules below | CF_ACCESS_TEAM |
| `identity` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `identity.oidc` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `identity.oidc.issuer_url` | string | required | Type; cross-file rules below | OIDC_ISSUER_URL |
| `identity.oidc.client_id` | string | required | Type; cross-file rules below | OIDC_CLIENT_ID |
| `identity.oidc.username_claim` | string | required | Type; cross-file rules below | OIDC_USERNAME_CLAIM |
| `identity.oidc.username_prefix` | string | required | Type; cross-file rules below | OIDC_USERNAME_PREFIX |
| `identity.oidc.groups_claim` | string | required | Type; cross-file rules below | OIDC_GROUPS_CLAIM |
| `identity.oidc.groups_prefix` | string | required | Type; cross-file rules below | OIDC_GROUPS_PREFIX |
| `identity.groups` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `identity.groups.platform_admin` | string | required | Type; cross-file rules below | GROUP_PLATFORM_ADMIN,OIDC_GROUP_PLATFORM_ADMIN |
| `identity.groups.auditor` | string | required | Type; cross-file rules below | GROUP_AUDITOR,OIDC_GROUP_AUDITOR |
| `identity.groups.breakglass` | string | required | Type; cross-file rules below | GROUP_BREAKGLASS,OIDC_GROUP_BREAKGLASS |
| `hostnames` | object | optional, no normaliser default | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `hostnames.panel` | string | "panel.<org.domain>" | Type; cross-file rules below | HOST_PANEL |
| `hostnames.argocd` | string | "argocd.<org.domain>" | Type; cross-file rules below | HOST_ARGOCD |
| `hostnames.grafana` | string | "grafana.<org.domain>" | Type; cross-file rules below | HOST_GRAFANA |
| `hostnames.headlamp` | string | "headlamp.<org.domain>" | Type; cross-file rules below | HOST_HEADLAMP |
| `hostnames.wazuh` | string | "wazuh.<org.domain>" | Type; cross-file rules below | HOST_WAZUH |
| `hostnames.mcp` | string | "mcp.<org.domain>" | Type; cross-file rules below | HOST_MCP |
| `alerting` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `alerting.default_receiver` | string | required | Type; cross-file rules below | ALERT_DEFAULT_RECEIVER |
| `alerting.receivers` | array | required | Type; cross-file rules below | ALERT_RECEIVERS_JSON |
| `alerting.receivers.[].name` | string | required | Type; cross-file rules below | ALERT_RECEIVERS_JSON |
| `alerting.receivers.[].kind` | string | required | Type; cross-file rules below | ALERT_RECEIVERS_JSON |
| `alerting.receivers.[].secret` | string | required | Type; cross-file rules below | ALERT_RECEIVERS_JSON |
| `alerting.receivers.[].key` | string | required | Type; cross-file rules below | ALERT_RECEIVERS_JSON |
| `alerting.quiet_hours` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `alerting.quiet_hours.start` | string | required | Type; cross-file rules below | ALERT_QUIET_START |
| `alerting.quiet_hours.end` | string | required | Type; cross-file rules below | ALERT_QUIET_END |
| `alerting.quiet_hours.timezone` | string | required | Type; cross-file rules below | ALERT_QUIET_TZ |
| `alerting.heartbeat` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `alerting.heartbeat.enabled` | boolean | required | Type; cross-file rules below | HEARTBEAT_ENABLED |
| `alerting.heartbeat.secret` | string | required | Type; cross-file rules below | HEARTBEAT_SECRET |
| `alerting.heartbeat.key` | string | required | Type; cross-file rules below | HEARTBEAT_SECRET_KEY |
| `backup` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `backup.kind` | string | required | Type; cross-file rules below | BACKUP_KIND |
| `backup.schedule` | string | required | Type; cross-file rules below | BACKUP_SCHEDULE |
| `backup.restic` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `backup.restic.sftp_host` | string | required | Type; cross-file rules below | BACKUP_SFTP_HOST |
| `backup.restic.sftp_user` | string | required | Type; cross-file rules below | BACKUP_SFTP_USER |
| `backup.restic.sftp_port` | integer | required | Type; cross-file rules below | BACKUP_SFTP_PORT |
| `backup.restic.repository_path` | string | required | Type; cross-file rules below | BACKUP_REPOSITORY_PATH |
| `backup.restic.secret` | string | required | Type; cross-file rules below | BACKUP_SECRET |
| `backup.never_back_up` | array | required | Type; cross-file rules below | BACKUP_EXCLUDE_JSON |
| `vendors` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `vendors.anthropic` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `vendors.anthropic.enabled` | boolean | required | Type; cross-file rules below | VENDOR_ANTHROPIC_ENABLED |
| `vendors.anthropic.tool` | string | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `vendors.anthropic.cli_version` | string | required | Type; cross-file rules below | CLAUDE_CLI_VERSION |
| `vendors.anthropic.org_uuid` | string | required | Type; cross-file rules below | CLAUDE_ORG_UUID |
| `vendors.anthropic.egress` | string | required | Type; cross-file rules below | CLAUDE_EGRESS |
| `vendors.openai` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `vendors.openai.enabled` | boolean | required | Type; cross-file rules below | VENDOR_OPENAI_ENABLED |
| `vendors.openai.tool` | string | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `vendors.openai.cli_version` | string | required | Type; cross-file rules below | CODEX_CLI_VERSION |
| `vendors.openai.workspace_id` | string | required | Type; cross-file rules below | CODEX_WORKSPACE_ID |
| `vendors.openai.egress` | string | required | Type; cross-file rules below | CODEX_EGRESS |
| `vendors.moonshot` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `vendors.moonshot.enabled` | boolean | required | Type; cross-file rules below | VENDOR_MOONSHOT_ENABLED |
| `vendors.moonshot.tool` | string | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `vendors.moonshot.cli_version` | string | required | Type; cross-file rules below | KIMI_CLI_VERSION |
| `vendors.moonshot.egress` | string | required | Type; cross-file rules below | KIMI_EGRESS |
| `vendors.local` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `vendors.local.enabled` | boolean | required | Type; cross-file rules below | VENDOR_LOCAL_ENABLED |
| `sessions` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `sessions.default_tier` | string | required | Type; cross-file rules below | SESSION_DEFAULT_TIER |
| `sessions.tiers` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `sessions.tiers.standard` | object | optional, no normaliser default | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `sessions.tiers.standard.pods` | integer | required | Type; cross-file rules below | TIER_PODS |
| `sessions.tiers.standard.statefulsets` | integer | required | Type; cross-file rules below | TIER_STATEFULSETS |
| `sessions.tiers.standard.pvcs` | integer | required | Type; cross-file rules below | TIER_PVCS |
| `sessions.tiers.standard.requests_cpu` | string | required | Type; cross-file rules below | TIER_REQUESTS_CPU |
| `sessions.tiers.standard.requests_memory` | string | required | Type; cross-file rules below | TIER_REQUESTS_MEMORY |
| `sessions.tiers.standard.limits_cpu` | string | required | Type; cross-file rules below | TIER_LIMITS_CPU |
| `sessions.tiers.standard.limits_memory` | string | required | Type; cross-file rules below | TIER_LIMITS_MEMORY |
| `sessions.tiers.standard.max_replicas_per_tool` | integer | required | Type; cross-file rules below | TIER_MAX_REPLICAS_PER_TOOL |
| `sessions.tiers.power` | object | optional, no normaliser default | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `sessions.tiers.power.pods` | integer | required | Type; cross-file rules below | TIER_PODS |
| `sessions.tiers.power.statefulsets` | integer | required | Type; cross-file rules below | TIER_STATEFULSETS |
| `sessions.tiers.power.pvcs` | integer | required | Type; cross-file rules below | TIER_PVCS |
| `sessions.tiers.power.requests_cpu` | string | required | Type; cross-file rules below | TIER_REQUESTS_CPU |
| `sessions.tiers.power.requests_memory` | string | required | Type; cross-file rules below | TIER_REQUESTS_MEMORY |
| `sessions.tiers.power.limits_cpu` | string | required | Type; cross-file rules below | TIER_LIMITS_CPU |
| `sessions.tiers.power.limits_memory` | string | required | Type; cross-file rules below | TIER_LIMITS_MEMORY |
| `sessions.tiers.power.max_replicas_per_tool` | integer | required | Type; cross-file rules below | TIER_MAX_REPLICAS_PER_TOOL |
| `sessions.idle_stop_minutes` | integer | required | Type; cross-file rules below | SESSION_IDLE_STOP_MINUTES |
| `sessions.lease_fail_closed` | boolean | required | Type; cross-file rules below | SESSION_LEASE_FAIL_CLOSED |
| `policy` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `policy.data_classes` | array | required | Type; cross-file rules below | DATA_CLASSES_JSON |
| `policy.permission_modes_allowed` | array | required | Type; cross-file rules below | PERMISSION_MODES_ALLOWED_JSON |
| `policy.default_permission_mode` | string | required | Type; cross-file rules below | DEFAULT_PERMISSION_MODE |
| `files` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `files.teams` | string | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `files.users` | string | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `files.accounts` | string | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `files.estates` | string | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `files.mcp_registry` | string | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `files.context_sources` | string | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `modules` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `components` | object | {} | Type; cross-file rules below | Model/directory only; no direct placeholder |

### teams.yaml fields

| Field | Type | Default | Allowed values / validation | Placeholder keys produced |
| --- | --- | --- | --- | --- |
| `version` | integer | required | constant 1 | Model/directory only; no direct placeholder |
| `teams` | array | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `teams.[].id` | string | required | Type; cross-file rules below | TEAM_ID,ENTITY_ID |
| `teams.[].idp_group` | string | required | Type; cross-file rules below | TEAM_IDP_GROUP,TEAM_OIDC_GROUP |
| `teams.[].leads` | array | required | Type; cross-file rules below | TEAM_LEADS_JSON |
| `teams.[].weight` | integer | required | Type; cross-file rules below | TEAM_WEIGHT |
| `teams.[].session_tier` | string | required | Type; cross-file rules below | TEAM_SESSION_TIER |
| `teams.[].permission_mode` | string | required | Type; cross-file rules below | TEAM_PERMISSION_MODE |
| `teams.[].data_classes_allowed` | array | required | Type; cross-file rules below | TEAM_DATA_CLASSES_JSON |
| `teams.[].vendors_allowed` | object | required | Type; cross-file rules below | TEAM_VENDORS_ALLOWED_JSON |
| `teams.[].vendors_allowed.public` | array | optional, no normaliser default | Type; cross-file rules below | TEAM_VENDORS_ALLOWED_JSON |
| `teams.[].vendors_allowed.internal` | array | optional, no normaliser default | Type; cross-file rules below | TEAM_VENDORS_ALLOWED_JSON |
| `teams.[].vendors_allowed.confidential` | array | optional, no normaliser default | Type; cross-file rules below | TEAM_VENDORS_ALLOWED_JSON |
| `teams.[].vendors_allowed.restricted` | array | optional, no normaliser default | Type; cross-file rules below | TEAM_VENDORS_ALLOWED_JSON |
| `teams.[].mcp_servers` | array | required | Type; cross-file rules below | TEAM_MCP_SERVERS_JSON |
| `teams.[].context_sources` | array | required | Type; cross-file rules below | TEAM_CONTEXT_SOURCES_JSON |
| `teams.[].litellm` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `teams.[].litellm.models` | array | required | Type; cross-file rules below | TEAM_LITELLM_MODELS_JSON |
| `teams.[].litellm.max_budget_usd` | integer | required | Type; cross-file rules below | TEAM_LITELLM_MAX_BUDGET_USD |
| `teams.[].litellm.budget_duration` | string | required | Type; cross-file rules below | TEAM_LITELLM_BUDGET_DURATION |
| `teams.[].litellm.tpm_limit` | integer | required | Type; cross-file rules below | TEAM_LITELLM_TPM |
| `teams.[].litellm.rpm_limit` | integer | required | Type; cross-file rules below | TEAM_LITELLM_RPM |
| `teams.[].litellm.task_budget_usd` | object | required | Type; cross-file rules below | TEAM_TASK_BUDGET_JSON |
| `teams.[].litellm.task_budget_usd.standard` | integer | optional, no normaliser default | Type; cross-file rules below | TEAM_TASK_BUDGET_JSON |
| `teams.[].litellm.task_budget_usd.bulk` | integer | optional, no normaliser default | Type; cross-file rules below | TEAM_TASK_BUDGET_JSON |
| `teams.[].factory` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `teams.[].factory.max_inflight_per_lane` | integer | required | Type; cross-file rules below | TEAM_FACTORY_MAX_INFLIGHT_PER_LANE |
| `teams.[].factory.queue_priority_ceiling` | integer | required | Type; cross-file rules below | TEAM_FACTORY_PRIORITY_CEILING |
| `teams.[].pools` | array | required | Type; cross-file rules below | TEAM_POOLS_JSON |

### users.yaml fields

| Field | Type | Default | Allowed values / validation | Placeholder keys produced |
| --- | --- | --- | --- | --- |
| `version` | integer | required | constant 1 | Model/directory only; no direct placeholder |
| `users` | array | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `users.[].slug` | string | required | pattern ^[a-z][a-z0-9-]{0,30}[a-z0-9]$ | USER_SLUG,USER_NS,ENTITY_ID |
| `users.[].oidc_sub` | string | required | Type; cross-file rules below | USER_OIDC_SUB,USER_OIDC_SUBJECT |
| `users.[].email` | string | required | Type; cross-file rules below | USER_EMAIL |
| `users.[].github` | string | null | Type; cross-file rules below | USER_GITHUB |
| `users.[].teams` | array | required | Type; cross-file rules below | USER_TEAMS_JSON |
| `users.[].primary_team` | string | required | Type; cross-file rules below | USER_PRIMARY_TEAM |
| `users.[].tier` | string / null | null | Type; cross-file rules below | USER_TIER |
| `users.[].status` | string | "active" | allowed: active, suspended, offboarded | USER_STATUS,USER_SUSPENDED |
| `users.[].tools` | object | required | Type; cross-file rules below | USER_TOOLS_JSON |
| `users.[].tools.claude` | object | optional, no normaliser default | Type; cross-file rules below | USER_TOOLS_JSON |
| `users.[].tools.claude.account` | string | required | Type; cross-file rules below | TOOL_ACCOUNT_ID,TOOL_VENDOR,TOOL_MAX_SESSIONS |
| `users.[].tools.claude.home_node` | string | required | Type; cross-file rules below | TOOL_HOME_NODE,TOOL_HOME_NODE_SHORT,TOOL_STS_NAME,TOOL_HOME_CLAIM |
| `users.[].tools.claude.replicas` | integer | required | Type; cross-file rules below | TOOL_REPLICAS |
| `users.[].tools.codex` | object | optional, no normaliser default | Type; cross-file rules below | USER_TOOLS_JSON |
| `users.[].tools.codex.account` | string | required | Type; cross-file rules below | TOOL_ACCOUNT_ID,TOOL_VENDOR,TOOL_MAX_SESSIONS |
| `users.[].tools.codex.home_node` | string | required | Type; cross-file rules below | TOOL_HOME_NODE,TOOL_HOME_NODE_SHORT,TOOL_STS_NAME,TOOL_HOME_CLAIM |
| `users.[].tools.codex.replicas` | integer | required | Type; cross-file rules below | TOOL_REPLICAS |
| `users.[].tools.kimi` | object | optional, no normaliser default | Type; cross-file rules below | USER_TOOLS_JSON |
| `users.[].tools.kimi.account` | string | required | Type; cross-file rules below | TOOL_ACCOUNT_ID,TOOL_VENDOR,TOOL_MAX_SESSIONS |
| `users.[].tools.kimi.home_node` | string | required | Type; cross-file rules below | TOOL_HOME_NODE,TOOL_HOME_NODE_SHORT,TOOL_STS_NAME,TOOL_HOME_CLAIM |
| `users.[].tools.kimi.replicas` | integer | required | Type; cross-file rules below | TOOL_REPLICAS |
| `tombstones` | array | [] | Type; cross-file rules below | Model/directory only; no direct placeholder |

### accounts.yaml fields

| Field | Type | Default | Allowed values / validation | Placeholder keys produced |
| --- | --- | --- | --- | --- |
| `version` | integer | required | constant 1 | Model/directory only; no direct placeholder |
| `default_policy` | object | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `default_policy.cap_pct` | integer | required | Type; cross-file rules below | PACE_CAP_PCT,ACCOUNT_CAP_PCT |
| `default_policy.reserve_pct` | integer | required | Type; cross-file rules below | PACE_RESERVE_PCT,ACCOUNT_RESERVE_PCT |
| `default_policy.min_session_spacing_s` | integer | required | Type; cross-file rules below | PACE_MIN_SESSION_SPACING_S |
| `plans` | array | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `plans.[].id` | string | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `plans.[].vendor` | string | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `plans.[].type` | string | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `accounts` | array | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `accounts.[].id` | string | required | Type; cross-file rules below | ACCOUNT_ID,ENTITY_ID |
| `accounts.[].vendor` | string | required | Type; cross-file rules below | ACCOUNT_VENDOR |
| `accounts.[].type` | string | required | allowed: seat, api, pool | ACCOUNT_TYPE |
| `accounts.[].plan` | string | required | Type; cross-file rules below | ACCOUNT_PLAN |
| `accounts.[].holder` | string / null | null | Type; cross-file rules below | ACCOUNT_HOLDER |
| `accounts.[].max_concurrent_sessions` | integer / null | null | Type; cross-file rules below | ACCOUNT_MAX_CONCURRENT_SESSIONS |
| `accounts.[].windows` | array | required | Type; cross-file rules below | ACCOUNT_WINDOWS_JSON |
| `accounts.[].usage_source` | string | required | Type; cross-file rules below | ACCOUNT_USAGE_SOURCE |
| `accounts.[].timezone` | string | "<org.timezone>" | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `accounts.[].owner_team` | null / string | null | Type; cross-file rules below | ACCOUNT_OWNER_TEAM |
| `accounts.[].shared_with` | array | [] | Type; cross-file rules below | ACCOUNT_SHARED_WITH_JSON |
| `accounts.[].policy` | object | {} | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `accounts.[].policy.cap_pct` | integer | optional, no normaliser default | Type; cross-file rules below | ACCOUNT_CAP_PCT |
| `accounts.[].policy.reserve_pct` | integer | optional, no normaliser default | Type; cross-file rules below | ACCOUNT_RESERVE_PCT |
| `accounts.[].policy.min_session_spacing_s` | integer | optional, no normaliser default | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `accounts.[].secret` | object / null | null | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `accounts.[].secret.namespace` | string | required | Type; cross-file rules below | ACCOUNT_SECRET_NAMESPACE |
| `accounts.[].secret.name` | string | required | Type; cross-file rules below | ACCOUNT_SECRET_NAME |
| `accounts.[].secret.key` | string | required | Type; cross-file rules below | ACCOUNT_SECRET_KEY |
| `accounts.[].litellm_models` | array | [] | Type; cross-file rules below | ACCOUNT_LITELLM_MODELS_JSON |
| `accounts.[].daily_cap_pct_of_week` | integer | optional, no normaliser default | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `accounts.[].budget_usd_per_month` | integer | optional, no normaliser default | Type; cross-file rules below | Model/directory only; no direct placeholder |

### estates.yaml fields

| Field | Type | Default | Allowed values / validation | Placeholder keys produced |
| --- | --- | --- | --- | --- |
| `version` | integer | required | constant 1 | Model/directory only; no direct placeholder |
| `deny_globs` | array | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `estates` | array | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `estates.[].id` | string | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `estates.[].owner_team` | string | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `estates.[].data_class` | string | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `estates.[].repos` | array | required | Type; cross-file rules below | Model/directory only; no direct placeholder |
| `estates.[].snapshot_targets` | array | required | Type; cross-file rules below | Model/directory only; no direct placeholder |

### Module fields

All module entries are optional. Missing modules are gated off; declared settings have no implicit
normaliser default unless supplied by a defaults file. Extra scalar/map/list fields are allowed.

| Field | Type | Default | Allowed values / validation | Placeholder keys produced |
| --- | --- | --- | --- | --- |
| `modules.factory.enabled` | boolean | none (example: false) | Type; enabled controls rendering | M_FACTORY_ENABLED |
| `modules.gpu-lanes.enabled` | boolean | none (example: false) | Type; enabled controls rendering | M_GPU_LANES_ENABLED |
| `modules.session-jobs.enabled` | boolean | none (example: false) | Type; enabled controls rendering | M_SESSION_JOBS_ENABLED |
| `modules.pkg-mirror.enabled` | boolean | none (example: false) | Type; enabled controls rendering | M_PKG_MIRROR_ENABLED |
| `modules.wazuh.enabled` | boolean | none (example: false) | Type; enabled controls rendering | M_WAZUH_ENABLED |
| `modules.hostwatch.enabled` | boolean | none (example: false) | Type; enabled controls rendering | M_HOSTWATCH_ENABLED |
| `modules.seccomp-gvisor.enabled` | boolean | none (example: false) | Type; enabled controls rendering | M_SECCOMP_GVISOR_ENABLED |
| `modules.hostguard.enabled` | boolean | none (example: false) | Type; enabled controls rendering | M_HOSTGUARD_ENABLED |
| `modules.hermes-ops-chat.enabled` | boolean | none (example: false) | Type; enabled controls rendering | M_HERMES_OPS_CHAT_ENABLED |
| `modules.workstation-lane.enabled` | boolean | none (example: false) | Type; enabled controls rendering | M_WORKSTATION_LANE_ENABLED |
| `modules.workstation-lane.window_start` | string | none (example: "19:00") | Type; enabled controls rendering | M_WORKSTATION_LANE_WINDOW_START |
| `modules.workstation-lane.window_stop` | string | none (example: "08:00") | Type; enabled controls rendering | M_WORKSTATION_LANE_WINDOW_STOP |
| `modules.workstation-lane.drain_minutes` | integer | none (example: 5) | Type; enabled controls rendering | M_WORKSTATION_LANE_DRAIN_MINUTES |
| `modules.workstation-lane.timezone` | string | none (example: "UTC") | Type; enabled controls rendering | M_WORKSTATION_LANE_TIMEZONE |
| `modules.arc-ci.enabled` | boolean | none (example: false) | Type; enabled controls rendering | M_ARC_CI_ENABLED |
| `modules.arc-ci.runner_light` | string | none (example: "arc-light") | Type; enabled controls rendering | M_ARC_CI_RUNNER_LIGHT |
| `modules.arc-ci.runner_heavy` | string | none (example: "arc-heavy") | Type; enabled controls rendering | M_ARC_CI_RUNNER_HEAVY |
| `modules.k3s-baremetal.enabled` | boolean | none (example: false) | Type; enabled controls rendering | M_K3S_BAREMETAL_ENABLED |
| `modules.k3s-maintenance.enabled` | boolean | none (example: false) | Type; enabled controls rendering | M_K3S_MAINTENANCE_ENABLED |

### Dynamic fields and derived values

`modules.<name>.<key>` and `components.<name>.<key>` accept scalar, map and list settings.
Flattening uppercases names and replaces punctuation with underscores, recursively joins map paths,
and appends `_JSON` for lists. `enabled` is boolean; disabled modules render no files or plugins.
Defaults files declare `scope: components|modules`, `name`, and a `defaults` mapping. Duplicate declarations
for the same scope/name are errors. Deep merging preserves defaults while explicit org scalars/lists replace them.

`hostnames` defaults each missing entry to `<service>.<org.domain>`.
`users[].tier` resolves through the primary team's session tier, then the session default tier.
`USER_PERMISSION_MODE` resolves through the primary team, then the policy default.
`USER_DATA_CLASSES_JSON` is the policy-ordered union of membership classes; MCP and context entitlements
are sorted unions. `TEAM_MEMBERS_JSON` contains sorted non-offboarded member slugs.
`TEAM_IDS_JSON`, `USER_SLUGS_JSON` and `MCP_SERVER_NAMES_JSON` are sorted identifiers.
`IMAGE_REPO_BASE` combines registry host/namespace and project image prefix; each image ref adds its
image name, tag and SHA256 digest. `NODE_IS_SESSION` and `NODE_IS_GPU` derive from node roles.
`SESSION_NODES_JSON` lists session nodes in sorted order. `NODE_PUBLIC_IPS_JSON` contains public IP `/32`
ranges. `SESSION_EGRESS_DENY_CIDRS_JSON` deduplicates private CIDRs, overlay, loopback, link-local,
node public IP ranges and configured extra deny ranges, preserving first occurrence.

Tool entries use `claude`, `codex`, or `kimi`; their vendors come from `vendors.<vendor>.tool`.
Only enabled vendors produce user-tool entities. `home_node: auto` uses SHA256 of `<slug>/<tool>`
modulo sorted session nodes and warns; pin the resulting node for stable login storage.
`TOOL_STS_NAME` is `<tool>-<node short>` and `TOOL_HOME_CLAIM` is `<tool>-home-<node short>`.
Suspension renders replicas as zero. Global booleans stringify to `true`/`false`; null becomes empty.
JSON placeholders use compact JSON with sorted dictionary keys and preserved list order.

### Cross-file validation rules

- Every document is version 1. Team IDs, user slugs, account IDs, estate IDs, node names, node short names,
  MCP server names and context-source names are unique within their respective lists.
- At least one node has `sessions` and at least one has `control-plane`. Node short names match
  `^[a-z][a-z0-9-]{0,14}$`. All nine image settings are required; digest is `sha256:` plus 64 lowercase hex
  characters. All-zero digests warn and fail strict mode.
- The default permission mode belongs to `policy.permission_modes_allowed`; the default session tier
  exists in `sessions.tiers`. Team permission modes and tiers obey the same catalogs.
- Team data classes belong to the policy catalog. Each `vendors_allowed` class is known and its vendors
  exist. MCP servers must exist and allow that team or `*`; context sources must exist. Pools must be
  API/pool accounts, owned by the team or shared with it or `*`. Team leads reference listed users.
- Accounts reference known vendors and have type `seat|api|pool`. Seats require a holder and integer
  `max_concurrent_sessions`. API/pool accounts require a known owner team; shared teams must exist or be
  `*`. API accounts require a secret namespace reference present in `org.namespaces`, excluding `user_prefix`.
  Plans, usage-source labels, budgets, percentages, windows and timezones are directory metadata;
  the reference normaliser does not impose additional ranges or catalog checks on them.
- User slugs match `^[a-z][a-z0-9-]{0,30}[a-z0-9]$`; prefix plus slug must fit 63 characters. Slugs cannot
  be tombstoned. Status is `active|suspended|offboarded`; memberships reference known teams and include
  `primary_team`. Explicit tiers must exist. Tool names are `claude|codex|kimi`; every account exists,
  is a seat held by that user, and matches the tool vendor. A seat cannot be assigned twice.
  Home nodes must be `auto` or a session node. Rendered tools cannot exceed tier or account replica limits.
- Estates have a known owner team and data class. Snapshot targets are known tools whose vendors are
  allowed by that team for that data class. Repository strings and deny globs are directory metadata.
- MCP transport is `stdio|http`; auth is `none|pod-identity`. Clients are `claude|codex|kimi|hermes`,
  with Kimi MCP disabled in v1. Environment names containing KEY, TOKEN, SECRET, PASSWORD or CREDENTIAL
  (case-insensitive) are forbidden. Stdio servers require `command` and `image_layer`, with neither
  `deploy` nor `service`. HTTP servers require exactly one of `deploy|service`, require authenticated access,
  and service namespace references must exist and exclude `user_prefix`.
- Context delivery is `push|mcp|feed`. MCP/feed sources require a known `served_by` server.
  MCP and context files are maintained by the MCP component; test-only canonical copies live under
  `tools/render/tests/fixtures/mcp/`.

### Setting conventions

The canonical examples recommend the following labels. These are configurable strings; the reference
normaliser enforces only the explicit cross-file rules above, so an unsupported integration must be
reviewed by the consuming component rather than inferred from successful rendering.

| Field | Conventional values |
| --- | --- |
| `cluster.distribution` | k3s, generic |
| `cluster.provider` | bare-metal, aws, gcp, azure, other |
| `cluster.cni` | kube-router, cilium, calico |
| `cluster.storage.login_storage` | disk, tmpfs |
| `nodes[].roles` | control-plane, worker, sessions, factory, gpu, ci |
| `network.overlay` | tailscale, none |
| `network.access.kind` | oidc-proxy, cloudflare-access, none |
| `backup.kind` | restic-sftp, velero, none |
| `vendors.<vendor>.egress` | public-443, gateway |
| `accounts[].usage_source` | manual, otel, codex-rollout, litellm |
| Times and schedules | quoted HH:MM strings; timezone is an IANA name |
| CIDRs, addresses, URLs, names, email, repository refs | strings interpreted by their consuming components |

### YAML subset

Block mappings and lists, comments, and single-line flow lists/maps of scalars are supported. Flow maps
may be block-list items. Double quotes support only escaped quote, backslash, newline and tab; single
quotes escape a quote by doubling it. Plain `true|false` are boolean, `null|~|empty` are null, and decimal
`-?[0-9]+` are integers; other accepted scalars are strings. Quote floats, yes/no/on/off, dates and
HH:MM-like times. Anchors, aliases, tags, multiline scalars, nested flow collections, tabs, duplicate
keys and invalid indentation are errors with source file, line and column. Use block maps for nesting.
Quote version strings that resemble floats. Quote non-JSON template values; use `_JSON` unquoted in YAML.
Literal template-shaped text uses `{{LBRACE2}}NAME}}`; other brace syntax such as Helm expressions passes through.

## Secrets

Provision values separately through the sealed-secret workflow. Accounts store namespace references,
secret names and key names only. Component `secrets.required.yaml` entries describe names, keys, purpose,
recipe and `required_when` conditions; rendering aggregates enabled entries into
`rendered/files/SECRETS-REQUIRED.md` and `.json`. `namespace_ref: user` expands per live user namespace.

## Deploy

Add a team with a unique ID, IdP group, leads, tier, permissions, entitlement lists, budgets and pools.
Add a user with a fresh permanent slug, immutable OIDC subject, email, memberships and primary team.
Create a seat account with vendor, plan, holder and explicit session concurrency, then reference it from
that user's tool entry with a pinned session home node and replicas. Create API accounts with an owner
team, optional sharing and a secret reference; list them in team pools. Add estates with explicit repositories,
classification, owner team and policy-approved snapshot targets. Review these changes like code.

Run `make render`, inspect the diff, and commit both the adopting org configuration and `rendered/`.
Argo CD syncs only manifest subtrees under `rendered/global`, `rendered/users` and `rendered/mcp`.
`rendered/files` contains scripts, values and JSON for separate consumers. `make check` detects drift.
`make validate` renders temporarily and invokes the offline manifest validator. No render command applies resources.

Offboarding first suspends a user while credentials are revoked and storage retention is handled separately.
Status `offboarded` omits user and tool outputs; stale rendered files disappear on the next render.
Remove the user entry and references from team leads and seat accounts, then add the permanent slug to
`tombstones`. A listed user, including offboarded status, cannot also be tombstoned. Never reuse tombstones.

## Verify

Run `python -m unittest discover -s tools/render/tests -t tools/render -v` and `make check`.
`--strict` refuses example zero digests, automatic home-node assignments and other warnings.
Schemas provide editor type checks; the normaliser enforces cross-file references and produces the
canonical fixture shape. `--print-model` exposes the complete non-secret directory for review.

## Rollback

Restore a reviewed Git revision of the org configuration and rendered tree, then render and check before
Argo sync. Credential revocation and namespace/storage cleanup require their own operational procedures.

## Security notes

Only an empty output directory or one with `.rendered-by-aa-render` may be written. Symlink output paths
are refused. Plugins may emit only under their own component directory with approved entity prefixes;
absolute paths, traversal and collisions are errors. Templates and plugins are trusted reviewed code.
No cluster credentials are required by the renderer.

## Full placeholder key list

Generated with `python tools/render/render.py --list-keys --markdown`. Global groups follow the shared
contract; entity groups list additions to global keys. Component keys depend on merged defaults.

### Global: project/org

`PROJECT_NAME` `LABEL_PREFIX` `IMAGE_PREFIX` `ORG_NAME` `ORG_DOMAIN` `ORG_TIMEZONE` `SECURITY_CONTACT`

### Global: GitHub/GitOps

`ORG_GITHUB_ORG` `ORG_GITHUB_REPO` `ORG_GIT_REMOTE` `GIT_REVISION` `CODEOWNERS_PLATFORM`

### Global: registry/images

`REGISTRY_HOST` `REGISTRY_NAMESPACE` `REGISTRY_PULL_SECRET` `IMAGE_REPO_BASE` `IMAGE_SESSION_CLAUDE` `IMAGE_SESSION_CODEX` `IMAGE_SESSION_KIMI` `IMAGE_PANEL` `IMAGE_SESSION_RUNNER` `IMAGE_PACE` `IMAGE_FARM_MCP` `IMAGE_FACTORY` `IMAGE_HERMES_CHAT`

### Global: cluster

`CLUSTER_DISTRIBUTION` `K8S_VERSION` `HOSTING_PROVIDER` `CNI` `APISERVER_ENDPOINT_IPS_JSON` `APISERVER_ENDPOINT_IP` `APISERVER_PORT` `APISERVER_SERVICE_IP` `APISERVER_URL` `POD_CIDR` `SERVICE_CIDR` `OVERLAY_CIDR` `PRIVATE_CIDRS_JSON` `NODE_PUBLIC_IPS_JSON` `SESSION_EGRESS_DENY_CIDRS_JSON` `CLUSTER_DNS_IP` `STORAGE_CLASS_DEFAULT` `STORAGE_CLASS_LOGIN` `LOGIN_HOST_ROOT` `LOGIN_STORAGE` `RUNTIME_CLASS_VM` `RUNTIME_CLASS_GVISOR` `RUNTIME_CLASS_GPU` `SESSION_NODES_JSON`

### Global: namespaces

`NS_SYSTEM` `NS_LLM` `NS_MCP` `NS_PORTAL` `NS_FACTORY` `NS_MODELS` `NS_EGRESS` `NS_SESSION_JOBS` `NS_OPS` `NS_MONITORING` `NS_ARGOCD` `USER_NS_PREFIX`

### Global: network/identity

`OVERLAY_KIND` `TAILNET_NAME` `TS_TAG_NODES` `ACCESS_KIND` `CF_ACCESS_TEAM` `OIDC_ISSUER_URL` `OIDC_CLIENT_ID` `OIDC_USERNAME_CLAIM` `OIDC_USERNAME_PREFIX` `OIDC_GROUPS_CLAIM` `OIDC_GROUPS_PREFIX` `GROUP_PLATFORM_ADMIN` `GROUP_AUDITOR` `GROUP_BREAKGLASS` `OIDC_GROUP_PLATFORM_ADMIN` `OIDC_GROUP_AUDITOR` `OIDC_GROUP_BREAKGLASS` `HOST_PANEL` `HOST_ARGOCD` `HOST_GRAFANA` `HOST_HEADLAMP` `HOST_WAZUH` `HOST_MCP`

### Global: alerting/backup

`ALERT_DEFAULT_RECEIVER` `ALERT_RECEIVERS_JSON` `ALERT_QUIET_START` `ALERT_QUIET_END` `ALERT_QUIET_TZ` `HEARTBEAT_ENABLED` `HEARTBEAT_SECRET` `HEARTBEAT_SECRET_KEY` `BACKUP_KIND` `BACKUP_SCHEDULE` `BACKUP_SFTP_HOST` `BACKUP_SFTP_USER` `BACKUP_SFTP_PORT` `BACKUP_REPOSITORY_PATH` `BACKUP_SECRET` `BACKUP_EXCLUDE_JSON`

### Global: vendors/sessions/policy

`VENDOR_ANTHROPIC_ENABLED` `VENDOR_OPENAI_ENABLED` `VENDOR_MOONSHOT_ENABLED` `VENDOR_LOCAL_ENABLED` `CLAUDE_CLI_VERSION` `CODEX_CLI_VERSION` `KIMI_CLI_VERSION` `CLAUDE_EGRESS` `CODEX_EGRESS` `KIMI_EGRESS` `CLAUDE_ORG_UUID` `CODEX_WORKSPACE_ID` `SESSION_DEFAULT_TIER` `SESSION_IDLE_STOP_MINUTES` `SESSION_LEASE_FAIL_CLOSED` `DATA_CLASSES_JSON` `PERMISSION_MODES_ALLOWED_JSON` `DEFAULT_PERMISSION_MODE` `PACE_CAP_PCT` `PACE_RESERVE_PCT` `PACE_MIN_SESSION_SPACING_S` `TEAM_IDS_JSON` `USER_SLUGS_JSON` `MCP_SERVER_NAMES_JSON`

### Global: modules

`M_ARC_CI_ENABLED` `M_ARC_CI_RUNNER_HEAVY` `M_ARC_CI_RUNNER_LIGHT` `M_FACTORY_ENABLED` `M_GPU_LANES_ENABLED` `M_HERMES_OPS_CHAT_ENABLED` `M_HOSTGUARD_ENABLED` `M_HOSTWATCH_ENABLED` `M_K3S_BAREMETAL_ENABLED` `M_K3S_MAINTENANCE_ENABLED` `M_PKG_MIRROR_ENABLED` `M_SECCOMP_GVISOR_ENABLED` `M_SESSION_JOBS_ENABLED` `M_WAZUH_ENABLED` `M_WORKSTATION_LANE_DRAIN_MINUTES` `M_WORKSTATION_LANE_ENABLED` `M_WORKSTATION_LANE_TIMEZONE` `M_WORKSTATION_LANE_WINDOW_START` `M_WORKSTATION_LANE_WINDOW_STOP`

### Global: components

Computed from merged component defaults as `C_<COMPONENT>_<KEY>`; lists append `_JSON`.

### Global: literal

`LBRACE2`

### user placeholders

`ENTITY_ID` `TIER_LIMITS_CPU` `TIER_LIMITS_MEMORY` `TIER_MAX_REPLICAS_PER_TOOL` `TIER_PODS` `TIER_PVCS` `TIER_REQUESTS_CPU` `TIER_REQUESTS_MEMORY` `TIER_STATEFULSETS` `USER_CONTEXT_SOURCES_JSON` `USER_DATA_CLASSES_JSON` `USER_EMAIL` `USER_GITHUB` `USER_MCP_SERVERS_JSON` `USER_NS` `USER_OIDC_SUB` `USER_OIDC_SUBJECT` `USER_PERMISSION_MODE` `USER_PRIMARY_TEAM` `USER_SLUG` `USER_STATUS` `USER_SUSPENDED` `USER_TEAMS_JSON` `USER_TIER` `USER_TOOLS_JSON`

### user-tool placeholders

`ENTITY_ID` `TIER_LIMITS_CPU` `TIER_LIMITS_MEMORY` `TIER_MAX_REPLICAS_PER_TOOL` `TIER_PODS` `TIER_PVCS` `TIER_REQUESTS_CPU` `TIER_REQUESTS_MEMORY` `TIER_STATEFULSETS` `TOOL` `TOOL_ACCOUNT_ID` `TOOL_HOME_CLAIM` `TOOL_HOME_NODE` `TOOL_HOME_NODE_SHORT` `TOOL_IMAGE` `TOOL_MAX_SESSIONS` `TOOL_REPLICAS` `TOOL_STS_NAME` `TOOL_VENDOR` `USER_CONTEXT_SOURCES_JSON` `USER_DATA_CLASSES_JSON` `USER_EMAIL` `USER_GITHUB` `USER_MCP_SERVERS_JSON` `USER_NS` `USER_OIDC_SUB` `USER_OIDC_SUBJECT` `USER_PERMISSION_MODE` `USER_PRIMARY_TEAM` `USER_SLUG` `USER_STATUS` `USER_SUSPENDED` `USER_TEAMS_JSON` `USER_TIER` `USER_TOOLS_JSON`

### team placeholders

`ENTITY_ID` `TEAM_CONTEXT_SOURCES_JSON` `TEAM_DATA_CLASSES_JSON` `TEAM_FACTORY_MAX_INFLIGHT_PER_LANE` `TEAM_FACTORY_PRIORITY_CEILING` `TEAM_ID` `TEAM_IDP_GROUP` `TEAM_LEADS_JSON` `TEAM_LITELLM_BUDGET_DURATION` `TEAM_LITELLM_MAX_BUDGET_USD` `TEAM_LITELLM_MODELS_JSON` `TEAM_LITELLM_RPM` `TEAM_LITELLM_TPM` `TEAM_MCP_SERVERS_JSON` `TEAM_MEMBERS_JSON` `TEAM_OIDC_GROUP` `TEAM_PERMISSION_MODE` `TEAM_POOLS_JSON` `TEAM_SESSION_TIER` `TEAM_TASK_BUDGET_JSON` `TEAM_VENDORS_ALLOWED_JSON` `TEAM_WEIGHT`

### node placeholders

`ENTITY_ID` `NODE_GPU_COUNT` `NODE_GPU_MODEL` `NODE_GPU_VRAM_GB` `NODE_IS_GPU` `NODE_IS_SESSION` `NODE_LABELS_JSON` `NODE_LAN_IP` `NODE_NAME` `NODE_OVERLAY_IP` `NODE_PUBLIC_IP` `NODE_ROLES_JSON` `NODE_RUNTIME_CLASSES_JSON` `NODE_SHORT`

### mcp placeholders

`ENTITY_ID` `MCP_ALLOWED_TEAMS_JSON` `MCP_APP_LABEL` `MCP_AUTH` `MCP_DESCRIPTION` `MCP_IMAGE` `MCP_NAME` `MCP_PATH` `MCP_PORT` `MCP_SERVER_EGRESS_JSON` `MCP_SERVER_SECRET_KEY` `MCP_SERVER_SECRET_NAME` `MCP_SERVICE_NAME` `MCP_TOOL_TIMEOUT_S`

### account placeholders

`ACCOUNT_CAP_PCT` `ACCOUNT_HOLDER` `ACCOUNT_ID` `ACCOUNT_LITELLM_MODELS_JSON` `ACCOUNT_MAX_CONCURRENT_SESSIONS` `ACCOUNT_OWNER_TEAM` `ACCOUNT_PLAN` `ACCOUNT_RESERVE_PCT` `ACCOUNT_SECRET_KEY` `ACCOUNT_SECRET_NAME` `ACCOUNT_SECRET_NAMESPACE` `ACCOUNT_SHARED_WITH_JSON` `ACCOUNT_TYPE` `ACCOUNT_USAGE_SOURCE` `ACCOUNT_VENDOR` `ACCOUNT_WINDOWS_JSON` `ENTITY_ID`


### Additional validation rules

In v1 `identity.oidc.username_claim` must be `sub`. Every node runtime class must be a configured `cluster.runtime_classes` value.

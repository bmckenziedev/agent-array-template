#!/usr/bin/env python3
"""Org defaults, validation and placeholder computation.

Semantics follow the canonical org normalisation contract.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from . import yamlsub

TOOLS = ("claude", "codex", "kimi")
IMAGE_KEYS = (
    "session-claude",
    "session-codex",
    "session-kimi",
    "panel",
    "session-runner",
    "pace",
    "farm-mcp",
    "factory",
    "hermes-chat",
)
CLIENTS = ("claude", "codex", "kimi", "hermes")
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{0,30}[a-z0-9]$")
SHORT_RE = re.compile(r"^[a-z][a-z0-9-]{0,14}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ZERO_DIGEST = "sha256:" + "0" * 64
SECRETISH = re.compile(r"(?i)(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)")


class OrgError(Exception):
    pass


def j(value) -> str:
    """JSON-valued placeholder: compact, sorted dict keys, list order kept."""
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def s(value) -> str:
    """Scalar placeholder value."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def up(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "_", name.upper())


def flatten(prefix: str, value, out: dict) -> None:
    if isinstance(value, dict):
        for k in sorted(value):
            flatten(f"{prefix}_{up(k)}", value[k], out)
    elif isinstance(value, list):
        out[prefix + "_JSON"] = j(value)
    else:
        out[prefix] = s(value)


def load(path: Path):
    return yamlsub.load(path)


def need(cond, msg):
    if not cond:
        raise OrgError(msg)


def normalise(root: Path, org_file: Path) -> dict:
    org = load(org_file)
    need(org.get("version") == 1, "org: version must be 1")
    warnings: list[str] = []
    merge_defaults(root, org)
    need(org["identity"]["oidc"]["username_claim"] == "sub",
         "identity.oidc.username_claim must be sub in v1")
    configured_runtimes = set(org["cluster"]["runtime_classes"].values())
    for node in org["nodes"]:
        need(set(node.get("runtime_classes", [])) <= configured_runtimes,
             f"node {node['name']}: runtime_classes must use configured names")
    files = org["files"]
    teams_doc = load(root / files["teams"])
    users_doc = load(root / files["users"])
    accounts_doc = load(root / files["accounts"])
    estates_doc = load(root / files["estates"])
    registry_doc = load(root / files["mcp_registry"])
    sources_doc = load(root / files["context_sources"])
    for name, doc in (
        ("teams", teams_doc),
        ("users", users_doc),
        ("accounts", accounts_doc),
        ("estates", estates_doc),
        ("mcp_registry", registry_doc),
        ("context_sources", sources_doc),
    ):
        need(doc.get("version") == 1, f"{name}: version must be 1")

    # ---- defaults
    for node in org["nodes"]:
        node.setdefault("public_ip", None)
        node.setdefault("lan_ip", None)
        node.setdefault("gpu", None)
        node.setdefault("runtime_classes", [])
        node.setdefault("labels", {})
    for key in ("panel", "argocd", "grafana", "headlamp", "wazuh", "mcp"):
        org.setdefault("hostnames", {}).setdefault(key, f"{key}.{org['org']['domain']}")
    org.setdefault("components", {})
    for user in users_doc["users"]:
        user.setdefault("tier", None)
        user.setdefault("status", "active")
        user.setdefault("github", None)
        user.setdefault("git", None)
        forge = user["git"]
        if forge is not None:
            need(isinstance(forge, dict) and set(forge) == {"credential_secret", "provider", "username"},
                 f"user {user['slug']}: git requires credential_secret, provider and username")
            need(forge["provider"] in ("github", "gitlab", "other"), "git: unsupported provider")
            need(isinstance(forge["credential_secret"], str) and
                 re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", forge["credential_secret"]),
                 "git: credential_secret must be a Secret name")
            need(isinstance(forge["username"], str) and
                 re.fullmatch(r"[A-Za-z0-9_.@-]+", forge["username"]), "git: invalid username")
    users_doc.setdefault("tombstones", [])
    for acct in accounts_doc["accounts"]:
        acct.setdefault("holder", None)
        acct.setdefault("owner_team", None)
        acct.setdefault("shared_with", [])
        acct.setdefault("max_concurrent_sessions", None)
        acct.setdefault("policy", {})
        acct.setdefault("secret", None)
        acct.setdefault("litellm_models", [])
        acct.setdefault("timezone", org["org"]["timezone"])
    for server in registry_doc["servers"]:
        server.setdefault("env", {})
        server.setdefault("args", [])
        server.setdefault("server_egress", [])
        server.setdefault("tools_readonly", [])
        server.setdefault("tool_timeout_s", 120)

    ns = org["namespaces"]
    teams = {t["id"]: t for t in teams_doc["teams"]}
    accounts = {a["id"]: a for a in accounts_doc["accounts"]}
    servers = {x["name"]: x for x in registry_doc["servers"]}
    sources = {x["name"]: x for x in sources_doc["sources"]}
    nodes = {n["name"]: n for n in org["nodes"]}
    vendors = org["vendors"]
    tool_vendor = {v["tool"]: name for name, v in vendors.items() if "tool" in v}
    classes = org["policy"]["data_classes"]
    modes = org["policy"]["permission_modes_allowed"]
    tiers = org["sessions"]["tiers"]

    # ---- validation
    def unique(items, key, what):
        seen = set()
        for it in items:
            need(it[key] not in seen, f"duplicate {what}: {it[key]}")
            seen.add(it[key])

    unique(teams_doc["teams"], "id", "team id")
    unique(users_doc["users"], "slug", "user slug")
    unique(accounts_doc["accounts"], "id", "account id")
    unique(org["nodes"], "name", "node name")
    unique(org["nodes"], "short", "node short")
    unique(registry_doc["servers"], "name", "mcp server")
    unique(sources_doc["sources"], "name", "context source")
    unique(estates_doc["estates"], "id", "estate id")
    session_nodes = sorted(n["name"] for n in org["nodes"] if "sessions" in n["roles"])
    need(session_nodes, "nodes: at least one node needs role 'sessions'")
    need(any("control-plane" in n["roles"] for n in org["nodes"]), "nodes: no control-plane node")
    for n in org["nodes"]:
        need(SHORT_RE.match(n["short"]), f"node {n['name']}: bad short name")
    for key in IMAGE_KEYS:
        need(key in org["images"], f"images: missing {key}")
        need(DIGEST_RE.match(org["images"][key]["digest"]), f"images.{key}: bad digest")
        if org["images"][key]["digest"] == ZERO_DIGEST:
            warnings.append(f"images.{key}: all-zero digest (refused by --strict)")
    need(org["policy"]["default_permission_mode"] in modes, "policy: default_permission_mode not allowed")
    need(org["sessions"]["default_tier"] in tiers, "sessions: default_tier unknown")

    for t in teams.values():
        need(t["permission_mode"] in modes, f"team {t['id']}: permission_mode not allowed")
        need(t["session_tier"] in tiers, f"team {t['id']}: unknown session_tier")
        need(set(t["data_classes_allowed"]) <= set(classes), f"team {t['id']}: unknown data class")
        for cls, vs in t["vendors_allowed"].items():
            need(cls in classes, f"team {t['id']}: vendors_allowed unknown class {cls}")
            need(set(vs) <= set(vendors), f"team {t['id']}: vendors_allowed unknown vendor")
        for srv in t["mcp_servers"]:
            need(srv in servers, f"team {t['id']}: unknown mcp server {srv}")
            allowed = servers[srv]["allowed_teams"]
            need(
                "*" in allowed or t["id"] in allowed, f"team {t['id']}: mcp server {srv} not allowed for team"
            )
        for src in t["context_sources"]:
            need(src in sources, f"team {t['id']}: unknown context source {src}")
        for pool in t["pools"]:
            need(pool in accounts, f"team {t['id']}: unknown pool {pool}")
            a = accounts[pool]
            need(a["type"] in ("api", "pool"), f"team {t['id']}: pool {pool} is a seat")
            need(
                a["owner_team"] == t["id"] or t["id"] in a["shared_with"] or "*" in a["shared_with"],
                f"team {t['id']}: pool {pool} not shared with team",
            )
        for lead in t["leads"]:
            need(any(u["slug"] == lead for u in users_doc["users"]), f"team {t['id']}: unknown lead {lead}")

    for a in accounts.values():
        if a.get("usage_source") == "otel":
            warnings.append(f"account {a['id']}: otel not implemented in v1")
        need(a["vendor"] in vendors, f"account {a['id']}: unknown vendor")
        need(a["type"] in ("seat", "api", "pool"), f"account {a['id']}: bad type")
        if a["type"] == "seat":
            need(a["holder"], f"account {a['id']}: seat needs holder")
            need(
                isinstance(a["max_concurrent_sessions"], int),
                f"account {a['id']}: seat needs max_concurrent_sessions",
            )
        else:
            need(a["owner_team"] in teams, f"account {a['id']}: owner_team unknown")
            for t in a["shared_with"]:
                need(t == "*" or t in teams, f"account {a['id']}: shared_with unknown team {t}")
        if a["type"] == "api":
            sec = a["secret"]
            need(
                sec and sec["namespace"] in ns and sec["namespace"] != "user_prefix",
                f"account {a['id']}: api account needs secret with a namespace ref",
            )

    seat_use: dict[str, str] = {}
    for u in users_doc["users"]:
        slug = u["slug"]
        need(SLUG_RE.match(slug), f"user {slug}: bad slug")
        need(len(ns["user_prefix"] + slug) <= 63, f"user {slug}: namespace too long")
        need(slug not in users_doc["tombstones"], f"user {slug}: slug is tombstoned")
        need(u["status"] in ("active", "suspended", "offboarded"), f"user {slug}: bad status")
        need(all(t in teams for t in u["teams"]), f"user {slug}: unknown team")
        need(u["primary_team"] in u["teams"], f"user {slug}: primary_team not in teams")
        if u["tier"] is not None:
            need(u["tier"] in tiers, f"user {slug}: unknown tier")
        for tool, spec in u["tools"].items():
            need(tool in TOOLS, f"user {slug}: unknown tool {tool}")
            acct = accounts.get(spec["account"])
            need(acct is not None, f"user {slug}: unknown account {spec['account']}")
            need(
                acct["type"] == "seat" and acct["holder"] == slug,
                f"user {slug}: {spec['account']} is not this user's seat",
            )
            need(
                acct["vendor"] == tool_vendor[tool],
                f"user {slug}: {spec['account']} vendor does not match {tool}",
            )
            need(spec["account"] not in seat_use, f"seat {spec['account']} used twice")
            seat_use[spec["account"]] = f"{slug}/{tool}"
            need(
                spec["home_node"] == "auto" or spec["home_node"] in session_nodes,
                f"user {slug}: home_node {spec['home_node']} is not a sessions node",
            )

    for e in estates_doc["estates"]:
        need(e["owner_team"] in teams, f"estate {e['id']}: unknown owner_team")
        need(e["data_class"] in classes, f"estate {e['id']}: unknown data_class")
        allowed = teams[e["owner_team"]]["vendors_allowed"].get(e["data_class"], [])
        for tool in e["snapshot_targets"]:
            need(tool in TOOLS, f"estate {e['id']}: unknown target {tool}")
            need(tool_vendor[tool] in allowed, f"estate {e['id']}: {tool} not allowed for {e['data_class']}")

    for x in registry_doc["servers"]:
        need(x["transport"] in ("stdio", "http"), f"mcp {x['name']}: bad transport")
        need(x["auth"] in ("none", "pod-identity"), f"mcp {x['name']}: auth {x['auth']} not supported in v1")
        need(set(x["clients"]) <= set(CLIENTS), f"mcp {x['name']}: unknown client")
        need("kimi" not in x["clients"], f"mcp {x['name']}: kimi MCP is disabled in v1")
        for envname in x["env"]:
            need(not SECRETISH.search(envname), f"mcp {x['name']}: env {envname} looks like a secret")
        if x["transport"] == "stdio":
            need("command" in x and "image_layer" in x, f"mcp {x['name']}: stdio needs command + image_layer")
            need("deploy" not in x and "service" not in x, f"mcp {x['name']}: stdio cannot deploy")
        else:
            need(
                ("deploy" in x) != ("service" in x),
                f"mcp {x['name']}: http needs exactly one of deploy/service",
            )
            need(x["auth"] != "none", f"mcp {x['name']}: http servers need auth")
            if "service" in x:
                need(
                    x["service"]["namespace_ref"] in ns and x["service"]["namespace_ref"] != "user_prefix",
                    f"mcp {x['name']}: bad namespace_ref",
                )
    for src in sources_doc["sources"]:
        need(src["delivery"] in ("push", "mcp", "feed"), f"source {src['name']}: bad delivery")
        if src["delivery"] in ("mcp", "feed"):
            need(src.get("served_by") in servers, f"source {src['name']}: served_by unknown")

    # ---- global keys
    k: dict[str, str] = {}
    p = org["project"]
    k.update(PROJECT_NAME=p["name"], LABEL_PREFIX=p["label_prefix"], IMAGE_PREFIX=p["image_prefix"])
    o = org["org"]
    k.update(
        ORG_NAME=o["name"],
        ORG_DOMAIN=o["domain"],
        ORG_TIMEZONE=o["timezone"],
        SECURITY_CONTACT=o["security_contact"],
    )
    g = org["github"]
    k.update(
        ORG_GITHUB_ORG=g["org"],
        ORG_GITHUB_REPO=g["repo"],
        ORG_GIT_REMOTE=g["git_remote"],
        GIT_REVISION=g["revision"],
        CODEOWNERS_PLATFORM=g["platform_team_handle"],
    )
    r = org["registry"]
    base = f"{r['host']}/{r['namespace']}/{p['image_prefix']}"
    k.update(
        REGISTRY_HOST=r["host"],
        REGISTRY_NAMESPACE=r["namespace"],
        REGISTRY_PULL_SECRET=r["pull_secret"],
        IMAGE_REPO_BASE=base,
    )
    for key in IMAGE_KEYS:
        im = org["images"][key]
        k["IMAGE_" + up(key)] = f"{base}-{key}:{im['tag']}@{im['digest']}"
    c = org["cluster"]
    k.update(
        CLUSTER_DISTRIBUTION=c["distribution"],
        K8S_VERSION=c["version"],
        HOSTING_PROVIDER=c["provider"],
        CNI=c["cni"],
    )
    a = c["apiserver"]
    k.update(
        APISERVER_ENDPOINT_IPS_JSON=j(a["endpoint_ips"]),
        APISERVER_ENDPOINT_IP=a["endpoint_ips"][0],
        APISERVER_PORT=s(a["port"]),
        APISERVER_SERVICE_IP=a["service_ip"],
        APISERVER_URL=a["url"],
    )
    cid = c["cidrs"]
    public32 = [f"{n['public_ip']}/32" for n in org["nodes"] if n["public_ip"]]
    deny = []
    for cidr in (
        cid["private"]
        + [cid["overlay"], "127.0.0.0/8", "169.254.0.0/16"]
        + public32
        + cid["session_egress_deny_extra"]
    ):
        if cidr not in deny:
            deny.append(cidr)
    k.update(
        POD_CIDR=cid["pod"],
        SERVICE_CIDR=cid["service"],
        OVERLAY_CIDR=cid["overlay"],
        PRIVATE_CIDRS_JSON=j(cid["private"]),
        NODE_PUBLIC_IPS_JSON=j(public32),
        SESSION_EGRESS_DENY_CIDRS_JSON=j(deny),
        CLUSTER_DNS_IP=c["dns_service_ip"],
    )
    st = c["storage"]
    k.update(
        STORAGE_CLASS_DEFAULT=st["default_class"],
        STORAGE_CLASS_LOGIN=st["login_class"],
        LOGIN_HOST_ROOT=st["login_host_root"],
        LOGIN_STORAGE=st["login_storage"],
    )
    rc = c["runtime_classes"]
    k.update(RUNTIME_CLASS_VM=rc["vm"], RUNTIME_CLASS_GVISOR=rc["gvisor"], RUNTIME_CLASS_GPU=rc["gpu"])
    for name, value in ns.items():
        if name == "user_prefix":
            k["USER_NS_PREFIX"] = value
        else:
            k["NS_" + up(name)] = value
    k["SESSION_NODES_JSON"] = j(session_nodes)
    net = org["network"]
    k.update(
        OVERLAY_KIND=net["overlay"],
        TAILNET_NAME=net["tailscale"]["tailnet"],
        TS_TAG_NODES=net["tailscale"]["node_tag"],
        ACCESS_KIND=net["access"]["kind"],
        CF_ACCESS_TEAM=s(net["access"].get("cloudflare_team")),
    )
    oi = org["identity"]["oidc"]
    k.update(
        OIDC_ISSUER_URL=oi["issuer_url"],
        OIDC_CLIENT_ID=oi["client_id"],
        OIDC_USERNAME_CLAIM=oi["username_claim"],
        OIDC_USERNAME_PREFIX=oi["username_prefix"],
        OIDC_GROUPS_CLAIM=oi["groups_claim"],
        OIDC_GROUPS_PREFIX=oi["groups_prefix"],
    )
    for gname, gval in org["identity"]["groups"].items():
        k["GROUP_" + up(gname)] = gval
        k["OIDC_GROUP_" + up(gname)] = oi["groups_prefix"] + gval
    for hname, hval in org["hostnames"].items():
        k["HOST_" + up(hname)] = hval
    al = org["alerting"]
    k.update(
        ALERT_DEFAULT_RECEIVER=al["default_receiver"],
        ALERT_RECEIVERS_JSON=j(al["receivers"]),
        ALERT_QUIET_START=al["quiet_hours"]["start"],
        ALERT_QUIET_END=al["quiet_hours"]["end"],
        ALERT_QUIET_TZ=al["quiet_hours"]["timezone"],
        HEARTBEAT_ENABLED=s(al["heartbeat"]["enabled"]),
        HEARTBEAT_SECRET=al["heartbeat"]["secret"],
        HEARTBEAT_SECRET_KEY=al["heartbeat"]["key"],
    )
    b = org["backup"]
    rs = b.get("restic") or {}
    k.update(
        BACKUP_KIND=b["kind"],
        BACKUP_SCHEDULE=b["schedule"],
        BACKUP_SFTP_HOST=s(rs.get("sftp_host")),
        BACKUP_SFTP_USER=s(rs.get("sftp_user")),
        BACKUP_SFTP_PORT=s(rs.get("sftp_port")),
        BACKUP_REPOSITORY_PATH=s(rs.get("repository_path")),
        BACKUP_SECRET=s(rs.get("secret")),
        BACKUP_EXCLUDE_JSON=j(b["never_back_up"]),
    )
    for vname, v in vendors.items():
        k[f"VENDOR_{up(vname)}_ENABLED"] = s(v["enabled"])
        if "tool" in v:
            k[f"{up(v['tool'])}_CLI_VERSION"] = v["cli_version"]
            k[f"{up(v['tool'])}_EGRESS"] = v["egress"]
    k["CLAUDE_ORG_UUID"] = vendors["anthropic"]["org_uuid"]
    k["CODEX_WORKSPACE_ID"] = vendors["openai"]["workspace_id"]
    se = org["sessions"]
    k.update(
        SESSION_DEFAULT_TIER=se["default_tier"],
        SESSION_IDLE_STOP_MINUTES=s(se["idle_stop_minutes"]),
        SESSION_LEASE_FAIL_CLOSED=s(se["lease_fail_closed"]),
    )
    k.update(
        DATA_CLASSES_JSON=j(classes),
        PERMISSION_MODES_ALLOWED_JSON=j(modes),
        DEFAULT_PERMISSION_MODE=org["policy"]["default_permission_mode"],
    )
    dp = accounts_doc["default_policy"]
    k.update(
        PACE_CAP_PCT=s(dp["cap_pct"]),
        PACE_RESERVE_PCT=s(dp["reserve_pct"]),
        PACE_MIN_SESSION_SPACING_S=s(dp["min_session_spacing_s"]),
    )
    live_users = [u for u in users_doc["users"] if u["status"] != "offboarded"]
    k.update(
        TEAM_IDS_JSON=j(sorted(teams)),
        USER_SLUGS_JSON=j(sorted(u["slug"] for u in live_users)),
        MCP_SERVER_NAMES_JSON=j(sorted(servers)),
    )
    for mname, mval in org["modules"].items():
        flatten("M_" + up(mname), mval, k)
    for cname, cval in org["components"].items():
        flatten("C_" + up(cname), cval, k)
    for key, value in k.items():
        if key.startswith("C_") and key.endswith("_IMAGE") and "@" + ZERO_DIGEST in value:
            warnings.append(f"{key}: all-zero digest (refused by --strict)")
    k["LBRACE2"] = "{{"

    # ---- entities
    ent = {"user": [], "user_tool": [], "team": [], "node": [], "mcp": [], "account": []}
    for t in teams_doc["teams"]:
        lit = t["litellm"]
        members = sorted(u["slug"] for u in live_users if t["id"] in u["teams"])
        ent["team"].append(
            {
                "ENTITY_ID": t["id"],
                "TEAM_ID": t["id"],
                "TEAM_IDP_GROUP": t["idp_group"],
                "TEAM_OIDC_GROUP": oi["groups_prefix"] + t["idp_group"],
                "TEAM_WEIGHT": s(t["weight"]),
                "TEAM_SESSION_TIER": t["session_tier"],
                "TEAM_PERMISSION_MODE": t["permission_mode"],
                "TEAM_LEADS_JSON": j(t["leads"]),
                "TEAM_MEMBERS_JSON": j(members),
                "TEAM_DATA_CLASSES_JSON": j(t["data_classes_allowed"]),
                "TEAM_VENDORS_ALLOWED_JSON": j(t["vendors_allowed"]),
                "TEAM_MCP_SERVERS_JSON": j(t["mcp_servers"]),
                "TEAM_CONTEXT_SOURCES_JSON": j(t["context_sources"]),
                "TEAM_POOLS_JSON": j(t["pools"]),
                "TEAM_LITELLM_MODELS_JSON": j(lit["models"]),
                "TEAM_LITELLM_MAX_BUDGET_USD": s(lit["max_budget_usd"]),
                "TEAM_LITELLM_BUDGET_DURATION": lit["budget_duration"],
                "TEAM_LITELLM_TPM": s(lit["tpm_limit"]),
                "TEAM_LITELLM_RPM": s(lit["rpm_limit"]),
                "TEAM_TASK_BUDGET_JSON": j(lit["task_budget_usd"]),
                "TEAM_FACTORY_MAX_INFLIGHT_PER_LANE": s(t["factory"]["max_inflight_per_lane"]),
                "TEAM_FACTORY_PRIORITY_CEILING": s(t["factory"]["queue_priority_ceiling"]),
            }
        )
    for n in org["nodes"]:
        gpu = n["gpu"] or {}
        ent["node"].append(
            {
                "ENTITY_ID": n["name"],
                "NODE_NAME": n["name"],
                "NODE_SHORT": n["short"],
                "NODE_ROLES_JSON": j(n["roles"]),
                "NODE_OVERLAY_IP": s(n["overlay_ip"]),
                "NODE_PUBLIC_IP": s(n["public_ip"]),
                "NODE_LAN_IP": s(n["lan_ip"]),
                "NODE_RUNTIME_CLASSES_JSON": j(n["runtime_classes"]),
                "NODE_LABELS_JSON": j(n["labels"]),
                "NODE_IS_SESSION": s("sessions" in n["roles"]),
                "NODE_IS_GPU": s("gpu" in n["roles"]),
                "NODE_GPU_MODEL": s(gpu.get("model")),
                "NODE_GPU_VRAM_GB": s(gpu.get("vram_gb")),
                "NODE_GPU_COUNT": s(gpu.get("count")),
            }
        )
    for u in live_users:
        prim = teams[u["primary_team"]]
        tier_name = u["tier"] or prim["session_tier"] or se["default_tier"]
        tier = tiers[tier_name]
        my_teams = [teams[t] for t in u["teams"]]
        user_classes = [cl for cl in classes if any(cl in t["data_classes_allowed"] for t in my_teams)]
        enabled_tools = sorted(t for t in u["tools"] if vendors[tool_vendor[t]]["enabled"])
        uk = {
            "ENTITY_ID": u["slug"],
            "USER_SLUG": u["slug"],
            "USER_NS": ns["user_prefix"] + u["slug"],
            "USER_OIDC_SUB": u["oidc_sub"],
            "USER_OIDC_SUBJECT": oi["username_prefix"] + u["oidc_sub"],
            "USER_EMAIL": u["email"],
            "USER_GITHUB": s(u["github"]),
            "USER_TEAMS_JSON": j(u["teams"]),
            "USER_PRIMARY_TEAM": u["primary_team"],
            "USER_TIER": tier_name,
            "USER_STATUS": u["status"],
            "USER_SUSPENDED": s(u["status"] == "suspended"),
            "USER_PERMISSION_MODE": prim["permission_mode"] or org["policy"]["default_permission_mode"],
            "USER_DATA_CLASSES_JSON": j(user_classes),
            "USER_MCP_SERVERS_JSON": j(sorted({x for t in my_teams for x in t["mcp_servers"]})),
            "USER_CONTEXT_SOURCES_JSON": j(sorted({x for t in my_teams for x in t["context_sources"]})),
            "USER_TOOLS_JSON": j(enabled_tools),
        }
        for tk, tv in tier.items():
            uk["TIER_" + up(tk)] = s(tv)
        forge = u.get("git") or {}
        uk["USER_GIT_SECRET"] = forge.get("credential_secret", "")
        uk["USER_GIT_VOLUME"] = ""
        uk["USER_GIT_MOUNT"] = ""
        uk["USER_GIT_ENV"] = ""
        if forge:
            uk["USER_GIT_VOLUME"] = "      - " + j({"name": "git-credential", "secret": {
                "secretName": forge["credential_secret"], "defaultMode": 288,
                "items": [{"key": "token", "path": "token"}]}})
            uk["USER_GIT_MOUNT"] = "        - " + j({"name": "git-credential",
                "mountPath": "/var/run/agent-array/git-credential", "readOnly": True})
            uk["USER_GIT_ENV"] = "\n".join("        - " + j({"name": name, "value": value})
                for name, value in (("AA_GIT_USERNAME", forge["username"]),
                                    ("AA_GIT_PROVIDER", forge["provider"]), ("AA_GIT_EMAIL", u["email"])))
        ent["user"].append(uk)
        for tool in enabled_tools:
            spec = u["tools"][tool]
            acct = accounts[spec["account"]]
            home = spec["home_node"]
            if home == "auto":
                idx = int(hashlib.sha256(f"{u['slug']}/{tool}".encode()).hexdigest(), 16) % len(session_nodes)
                home = session_nodes[idx]
                warnings.append(f"user {u['slug']}/{tool}: home_node auto -> {home}; pin it in users.yaml")
            need(
                spec["replicas"] <= tier["max_replicas_per_tool"],
                f"user {u['slug']}/{tool}: replicas above tier",
            )
            need(
                spec["replicas"] <= acct["max_concurrent_sessions"],
                f"user {u['slug']}/{tool}: replicas above account max",
            )
            short = nodes[home]["short"]
            tkeys = dict(uk)
            tkeys.update(
                {
                    "ENTITY_ID": f"{u['slug']}/{tool}",
                    "TOOL": tool,
                    "TOOL_VENDOR": tool_vendor[tool],
                    "TOOL_ACCOUNT_ID": acct["id"],
                    "TOOL_HOME_NODE": home,
                    "TOOL_HOME_NODE_SHORT": short,
                    "TOOL_REPLICAS": s(0 if u["status"] == "suspended" else spec["replicas"]),
                    "TOOL_STS_NAME": f"{tool}-{short}",
                    "TOOL_HOME_CLAIM": f"{tool}-home-{short}",
                    "TOOL_IMAGE": k["IMAGE_SESSION_" + up(tool)],
                    "TOOL_MAX_SESSIONS": s(acct["max_concurrent_sessions"]),
                }
            )
            ent["user_tool"].append(tkeys)
        for tool in sorted(set(u["tools"]) - set(enabled_tools)):
            warnings.append(f"user {u['slug']}: tool {tool} skipped (vendor disabled)")
    for x in registry_doc["servers"]:
        if x["transport"] != "http" or "deploy" not in x:
            continue
        d = x["deploy"]
        sec = x.get("server_secret") or {}
        ent["mcp"].append(
            {
                "ENTITY_ID": x["name"],
                "MCP_NAME": x["name"],
                "MCP_DESCRIPTION": x.get("description", ""),
                "MCP_IMAGE": d["image"],
                "MCP_PORT": s(d["port"]),
                "MCP_PATH": d.get("path", "/mcp"),
                "MCP_APP_LABEL": f"mcp-{x['name']}",
                "MCP_SERVICE_NAME": f"mcp-{x['name']}",
                "MCP_AUTH": x["auth"],
                "MCP_ALLOWED_TEAMS_JSON": j(x["allowed_teams"]),
                "MCP_SERVER_EGRESS_JSON": j(x["server_egress"]),
                "MCP_SERVER_SECRET_NAME": s(sec.get("name")),
                "MCP_SERVER_SECRET_KEY": s(sec.get("key")),
                "MCP_TOOL_TIMEOUT_S": s(x["tool_timeout_s"]),
            }
        )
    for a_ in accounts_doc["accounts"]:
        pol = dict(dp)
        pol.update(a_["policy"])
        sec = a_["secret"] or {}
        ent["account"].append(
            {
                "ENTITY_ID": a_["id"],
                "ACCOUNT_ID": a_["id"],
                "ACCOUNT_VENDOR": a_["vendor"],
                "ACCOUNT_TYPE": a_["type"],
                "ACCOUNT_PLAN": a_["plan"],
                "ACCOUNT_HOLDER": s(a_["holder"]),
                "ACCOUNT_OWNER_TEAM": s(a_["owner_team"]),
                "ACCOUNT_SHARED_WITH_JSON": j(a_["shared_with"]),
                "ACCOUNT_MAX_CONCURRENT_SESSIONS": s(a_["max_concurrent_sessions"]),
                "ACCOUNT_CAP_PCT": s(pol["cap_pct"]),
                "ACCOUNT_RESERVE_PCT": s(pol["reserve_pct"]),
                "ACCOUNT_WINDOWS_JSON": j(a_["windows"]),
                "ACCOUNT_USAGE_SOURCE": a_["usage_source"],
                "ACCOUNT_SECRET_NAMESPACE": s(ns.get(sec.get("namespace")) if sec else None),
                "ACCOUNT_SECRET_NAME": s(sec.get("name")),
                "ACCOUNT_SECRET_KEY": s(sec.get("key")),
                "ACCOUNT_LITELLM_MODELS_JSON": j(a_["litellm_models"]),
            }
        )

    return {
        "version": 1,
        "org": org,
        "teams": teams_doc["teams"],
        "users": users_doc["users"],
        "tombstones": users_doc["tombstones"],
        "accounts": {
            "default_policy": dp,
            "plans": accounts_doc["plans"],
            "accounts": accounts_doc["accounts"],
        },
        "estates": {"deny_globs": estates_doc["deny_globs"], "estates": estates_doc["estates"]},
        "mcp_registry": {"servers": registry_doc["servers"]},
        "context_sources": {"sources": sources_doc["sources"]},
        "keys": dict(sorted(k.items())),
        "entities": ent,
        "warnings": warnings,
    }


def deep_merge(defaults, overrides):
    """Merge mappings recursively; an explicit scalar/list replaces its default."""
    import copy

    result = copy.deepcopy(defaults)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def merge_defaults(root, org):
    from .templates import walk

    seen = set()
    for path in walk(root, gates=False):
        if path.name != "org.component.defaults.yaml":
            continue
        doc = load(path)
        scope, name = doc.get("scope"), doc.get("name")
        need(scope in ("components", "modules"), f"{path}: scope must be components or modules")
        need(isinstance(name, str) and name, f"{path}: name must be a nonempty string")
        need(isinstance(doc.get("defaults"), dict), f"{path}: defaults must be a mapping")
        need((scope, name) not in seen, f"{path}: duplicate defaults for {scope}.{name}")
        seen.add((scope, name))
        org.setdefault(scope, {})[name] = deep_merge(doc["defaults"], org.get(scope, {}).get(name, {}))


def load_model(root, org_file, strict=False):
    try:
        result = normalise(Path(root), Path(org_file))
        if strict and result["warnings"]:
            raise OrgError("; ".join(result["warnings"]))
        return result
    except yamlsub.YamlError:
        raise
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
        raise OrgError(f"{org_file}: invalid or missing field: {exc}") from exc
    except OrgError as exc:
        message = str(exc)
        source = Path(org_file)
        # Reference failures identify entities; report their actual input document too.
        category = message.removeprefix("duplicate ").split()[0].rstrip(":")
        references = {
            "team": "teams",
            "teams": "teams",
            "user": "users",
            "users": "users",
            "account": "accounts",
            "accounts": "accounts",
            "seat": "users",
            "estate": "estates",
            "estates": "estates",
            "mcp": "mcp_registry",
            "mcp_registry": "mcp_registry",
            "source": "context_sources",
            "context": "context_sources",
            "context_sources": "context_sources",
        }
        try:
            files = load(Path(org_file))["files"]
            if category in references:
                source = Path(root) / files[references[category]]
        except (OSError, KeyError, TypeError, yamlsub.YamlError):
            pass
        raise OrgError(f"{source}: {message}") from exc

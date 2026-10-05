"""Deterministic managed MCP configuration and context policy renderer."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

PLUGIN_NAME = "mcp"
KEY_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")
SECRETISH = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", re.I)


def subst(text: str, keys: dict[str, str]) -> str:
    def rep(match):
        if match.group(1) not in keys:
            raise KeyError(f"unknown placeholder {match.group(1)}")
        return keys[match.group(1)]
    return KEY_RE.sub(rep, text)


def stable(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def manifest(kind, name, namespace, spec, keys):
    # JSON is a YAML subset and avoids escaping arbitrary config data into block scalars.
    return stable({"apiVersion": "v1" if kind == "ConfigMap" else "networking.k8s.io/v1",
                   "kind": kind, "metadata": {"name": name, "namespace": namespace,
                   "labels": {"app.kubernetes.io/name": name,
                              "app.kubernetes.io/part-of": keys["PROJECT_NAME"],
                              "app.kubernetes.io/component": "mcp"}}, **spec})


def entitled(user, teams, registry, field):
    wanted = set().union(*(set(teams[t][field]) for t in user["teams"]))
    return [s for s in sorted(registry, key=lambda x: x["name"])
            if s["name"] in wanted and
            ("*" in s["allowed_teams"] or set(user["teams"]) & set(s["allowed_teams"]))]


def endpoint(server, model):
    if "deploy" in server:
        cfg = server["deploy"]
        return "mcp-" + server["name"], model["org"]["namespaces"]["mcp"], cfg
    cfg = server["service"]
    return cfg["name"], model["org"]["namespaces"][cfg["namespace_ref"]], cfg


def url(server, model):
    name, namespace, cfg = endpoint(server, model)
    return f"http://{name}.{namespace}.svc:{cfg['port']}{cfg['path']}"


def toml(servers, model, requirements=False):
    lines = []
    for s in servers:
        lines.append(f"[mcp_servers.{json.dumps(s['name'])}]")
        if requirements:
            identity = {"command": s["command"] if s["transport"] == "stdio" else "/usr/local/bin/aa-mcp-bridge"}
            parts = [f"{k} = {json.dumps(v)}" for k, v in identity.items()]
            lines.append("identity = { " + ", ".join(parts) + " }")
        else:
            if s["transport"] == "stdio":
                lines += ["command = " + json.dumps(s["command"]),
                          "args = " + json.dumps(s.get("args", []))]
                env = ", ".join(f"{json.dumps(k)} = {json.dumps(v)}" for k, v in sorted(s.get("env", {}).items()))
                lines.append("env = { " + env + " }")
            else:
                lines += ['command = "/usr/local/bin/aa-mcp-bridge"',
                          "args = " + json.dumps([url(s, model)])]
            lines.append(f"tool_timeout_sec = {s['tool_timeout_s']}")
            lines.append("startup_timeout_sec = 30")
        lines.append("")
    return "\n".join(lines)


def render(model, emit):
    keys = model["keys"]
    teams = {t["id"]: t for t in model["teams"]}
    servers = model["mcp_registry"]["servers"]
    if model["org"].get("modules", {}).get("factory", {}).get("enabled"):
        servers = [dict(server, env={**server.get("env", {}),
                   "FACTORY_API_URL": f"http://factory-api.{keys['NS_FACTORY']}.svc:8080"})
                   if server["name"] == "factory" else server for server in servers]
    for s in servers:
        if "kimi" in s["clients"]:
            raise ValueError("kimi MCP clients are disabled in v1")
        if s["auth"] not in ("none", "pod-identity") or (s["transport"] == "http" and s["auth"] == "none"):
            raise ValueError("unsupported MCP authentication")
        if any(SECRETISH.search(k) for k in s.get("env", {})):
            raise ValueError("credential-bearing env is forbidden")
    vendors = {v["tool"]: name for name, v in model["org"]["vendors"].items() if "tool" in v}
    entitlements = {}
    for user in sorted(model["users"], key=lambda u: u["slug"]):
        if user["status"] == "offboarded":
            continue
        slug = user["slug"]
        namespace = keys["USER_NS_PREFIX"] + slug
        prefix = f"users/{slug}/mcp/"
        allowed = entitled(user, teams, servers, "mcp_servers")
        entitlements[slug] = [s["name"] for s in allowed]
        enabled = {t for t in user["tools"] if model["org"]["vendors"][vendors[t]]["enabled"]}
        for tool in sorted(enabled):
            chosen = [s for s in allowed if tool in s["clients"]]
            supervised = model["org"].get("components", {}).get("supervisor", {}).get("enabled", False)
            if tool == "claude" and supervised:
                chosen = chosen + [{"name": "aa-permission", "transport": "stdio",
                                    "command": "/usr/local/bin/aa-permission-mcp", "args": [], "env": {}}]
            if tool == "kimi" or not chosen:
                continue
            if tool == "claude":
                entries = {}
                for s in chosen:
                    if s["transport"] == "stdio":
                        entries[s["name"]] = {"type": "stdio", "command": s["command"],
                                              "args": s.get("args", []), "env": s.get("env", {})}
                    else:
                        entries[s["name"]] = {"type": "http", "url": url(s, model),
                                              "headersHelper": "/usr/local/bin/aa-mcp-token"}
                data = {"managed-mcp.json": stable({"mcpServers": entries}),
                        "allowed-mcp-servers.json": stable([{"serverName": s["name"]} for s in chosen])}
            elif tool == "codex":
                data = {"requirements.mcp.toml": toml(chosen, model, True),
                        "managed_config.mcp.toml": toml(chosen, model)}
            else:
                raise ValueError(f"unsupported managed tool: {tool}")
            hashes = {k: hashlib.sha256(v.encode()).hexdigest() for k, v in data.items()}
            data["mcp-rendered.json"] = stable({"servers": [s["name"] for s in chosen], "sha256": hashes})
            emit(prefix + tool + "-mcp.yaml", manifest("ConfigMap", tool + "-mcp", namespace, {"data": data}, keys))
        estates = []
        for estate in sorted(model["estates"]["estates"], key=lambda e: e["id"]):
            if estate["owner_team"] not in user["teams"]:
                continue
            owner = teams[estate["owner_team"]]
            targets = sorted(t for t in estate["snapshot_targets"] if t in enabled and
                             vendors[t] in owner["vendors_allowed"].get(estate["data_class"], []) and
                             estate["data_class"] in owner["data_classes_allowed"])
            estates.append({"id": estate["id"], "data_class": estate["data_class"],
                            "repos": sorted(estate["repos"]), "snapshot_targets": targets,
                            "deny_globs": sorted(model["estates"]["deny_globs"])})
        sources = entitled(user, teams, model["context_sources"]["sources"], "context_sources")
        sources = [s for s in sources if s["delivery"] == "push" or s.get("served_by") in entitlements[slug]]
        data = {"estates.json": stable(estates), "sources.json": stable(sources)}
        emit(prefix + "context-policy.yaml", manifest("ConfigMap", "context-policy", namespace, {"data": data}, keys))
        for s in allowed:
            if s["transport"] != "http":
                continue
            name, ns, cfg = endpoint(s, model)
            spec = {"podSelector": {"matchLabels": {keys["LABEL_PREFIX"] + "/user": slug}},
                    "policyTypes": ["Egress"], "egress": [{"to": [{
                        "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": ns}},
                        "podSelector": {"matchLabels": {"app.kubernetes.io/name": name}}}],
                        "ports": [{"protocol": "TCP", "port": cfg["port"]}]}]}
            emit(prefix + "netpol-mcp-" + s["name"] + ".yaml",
                 manifest("NetworkPolicy", "mcp-egress-" + s["name"], namespace, {"spec": spec}, keys))
    # This subtree is plugin-owned (its RENDER-IF sentinel skips generic rendering).
    # Conditional Secret mounts cannot be expressed by scalar substitution alone.
    root = Path(__file__).parent
    for entity in sorted(model["entities"]["mcp"], key=lambda e: e["MCP_NAME"]):
        scope = {**keys, **entity}
        server = next(s for s in servers if s["name"] == entity["MCP_NAME"])
        for filename in ("deployment", "networkpolicy", "service", "rbac"):
            text = (root / "k8s/server" / (filename + ".per-mcp.tmpl.yaml")).read_text(encoding="utf-8")
            if filename == "deployment" and not server.get("server_secret"):
                text = text.replace('            - {name: upstream, mountPath: /etc/agent-array/upstream, readOnly: true}\n', '')
                text = text.split('        - name: upstream\n')[0]
            if filename == "networkpolicy":
                endpoints = json.loads(keys["APISERVER_ENDPOINT_IPS_JSON"])
                lines = ''.join('        - ipBlock: {cidr: ' + json.dumps(ip + '/32') + '}\n' for ip in endpoints)
                text = text.replace('        - ipBlock: {cidr: "{{APISERVER_ENDPOINT_IP}}/32"}\n', lines)
                if not server.get("server_egress"):
                    text = text.split('    # NetworkPolicy cannot enforce')[0]
            emit(f"mcp/{entity['MCP_NAME']}/mcp/k8s/server/{filename}.yaml", subst(text, scope))
    requirements = [{"server": s["name"], "namespace": keys["NS_MCP"],
                     "name": s["server_secret"]["name"], "keys": [s["server_secret"]["key"]]}
                    for s in sorted(servers, key=lambda s: s["name"])
                    if s.get("deploy") and s.get("server_secret")]
    emit("files/mcp/secrets-per-registry.json", stable(requirements))

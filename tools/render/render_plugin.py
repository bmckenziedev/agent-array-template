"""Publish the normalised non-secret directory for platform services."""

import json

PLUGIN_NAME = "org-directory"


def render(model, emit):
    data = {
        "users.json": [u for u in model["users"] if u["status"] != "offboarded"],
        "teams.json": model["teams"],
        "accounts.json": model["accounts"]["accounts"],
        "estates.json": model["estates"],
        "mcp-entitlements.json": {t["id"]: t["mcp_servers"] for t in model["teams"]},
        "context-sources.json": model["context_sources"]["sources"],
    }
    keys = model["keys"]
    for namespace in sorted({keys["NS_" + name] for name in ("SYSTEM", "MCP", "PORTAL", "LLM", "FACTORY")}):
        lines = [
            "apiVersion: v1",
            "kind: ConfigMap",
            "metadata:",
            "  name: org-directory",
            "  namespace: " + json.dumps(namespace),
            "  labels:",
            "    app.kubernetes.io/name: org-directory",
            "    app.kubernetes.io/part-of: " + json.dumps(keys["PROJECT_NAME"]),
            "    app.kubernetes.io/component: org-directory",
            "data:",
        ]
        for name, value in sorted(data.items()):
            encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
            lines.append("  " + name + ": " + json.dumps(encoded))
        emit(f"global/tools/render/k8s/org-directory-{namespace}.yaml", "\n".join(lines) + "\n")

"""Render the panel's conditional Kubernetes API access."""

import json
import re
from pathlib import Path

PLUGIN_NAME = "portal-panel-deployment"


def render(model, emit):
    keys = dict(model["keys"])
    defaults = model["org"].get("components", {}).get("panel", {})
    for name, value in defaults.items():
        key = "C_PANEL_" + name.upper()
        keys[key + "_JSON" if isinstance(value, list) else key] = (
            json.dumps(value) if isinstance(value, list) else str(value)
        )

    def replace(match):
        return json.dumps(str(keys[match.group(1)]))[1:-1]

    text = (
        Path(__file__).with_name("deployment.template.json").read_text(encoding="utf-8")
    )
    deployment = json.loads(re.sub(r"\{\{([A-Z][A-Z0-9_]*)\}\}", replace, text))
    spec = deployment["spec"]["template"]["spec"]
    for entry in spec["containers"][0]["env"]:
        if entry["name"] == "TASK_MODELS":
            entry["value"] = ",".join(
                defaults.get("task_models", ["local-coder", "local-coder-small"])
            )
    enabled = (
        model["org"].get("modules", {}).get("session-jobs", {}).get("enabled", False)
    )
    if not enabled:
        spec["containers"][0]["env"] = [
            entry
            for entry in spec["containers"][0]["env"]
            if entry["name"] != "LITELLM_MINT_KEY"
        ]
    if keys["ACCESS_KIND"] != "cloudflare-access":
        spec["containers"][0]["env"] = [
            entry
            for entry in spec["containers"][0]["env"]
            if entry["name"] != "CF_ACCESS_AUD"
        ]
    if enabled:
        spec["automountServiceAccountToken"] = True
        runtime = model["org"]["modules"]["session-jobs"].get("runtime", "kata")
        key = "RUNTIME_CLASS_VM" if runtime == "kata" else "RUNTIME_CLASS_GVISOR"
        spec["containers"][0]["env"].append(
            {"name": "SESSION_RUNTIME_CLASS", "value": keys[key]}
        )
        spec["containers"][0]["volumeMounts"].append(
            {"name": "template", "mountPath": "/app/template", "readOnly": True}
        )
        spec["volumes"].append(
            {"name": "template", "configMap": {"name": "session-job-template"}}
        )
    emit("global/portal/k8s/deployment.yaml", json.dumps(deployment, indent=2) + "\n")

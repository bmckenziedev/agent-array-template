"""Permit API access only when Jobs dispatch is enabled."""

import json

PLUGIN_NAME = "portal-job-api-egress"


def render(model, emit):
    if (
        not model["org"]
        .get("modules", {})
        .get("session-jobs", {})
        .get("enabled", False)
    ):
        return
    keys = model["keys"]
    ips = json.loads(keys["APISERVER_ENDPOINT_IPS_JSON"])
    rules = [
        {
            "to": [
                {"ipBlock": {"cidr": ip + ("/128" if ":" in ip else "/32")}}
                for ip in ips
            ],
            "ports": [{"protocol": "TCP", "port": int(keys["APISERVER_PORT"])}],
        },
        {
            "to": [{"ipBlock": {"cidr": keys["APISERVER_SERVICE_IP"] + "/32"}}],
            "ports": [{"protocol": "TCP", "port": 443}],
        },
    ]
    doc = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": "panel-job-api", "namespace": keys["NS_PORTAL"]},
        "spec": {
            "podSelector": {"matchLabels": {"app.kubernetes.io/name": "panel"}},
            "policyTypes": ["Egress"],
            "egress": rules,
        },
    }
    emit("global/portal/api-egress/k8s/policy.yaml", json.dumps(doc, indent=2) + "\n")

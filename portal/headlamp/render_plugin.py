"""Permit API access only when Jobs dispatch is enabled."""

import json

PLUGIN_NAME = "portal-headlamp-api-egress"


def render(model, emit):
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
        "metadata": {"name": "headlamp-api", "namespace": keys["NS_PORTAL"]},
        "spec": {
            "podSelector": {"matchLabels": {"app.kubernetes.io/name": "headlamp"}},
            "policyTypes": ["Egress"],
            "egress": rules,
        },
    }
    emit("global/portal/headlamp/k8s/policy.yaml", json.dumps(doc, indent=2) + "\n")

"""Service configuration only; user RBAC is rendered by the per-user template."""
import json

PLUGIN_NAME = "farm-mcp"


def render(model, emit):
    config = {"label_prefix": model["keys"]["LABEL_PREFIX"],
              "tiers": model["org"]["sessions"]["tiers"],
              "factory_enabled": model["org"]["modules"]["factory"]["enabled"],
              "estates": model["estates"]["estates"],
              "lanes": model["org"]["modules"].get("gpu-lanes", {}).get("lanes", [])}
    manifest = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {
        "name": "farm-mcp-config", "namespace": model["keys"]["NS_SYSTEM"],
        "labels": {"app.kubernetes.io/name": "farm-mcp", "app.kubernetes.io/part-of": model["keys"]["PROJECT_NAME"]}},
        "data": {"config.json": json.dumps(config, sort_keys=True, separators=(",", ":")) + "\n"}}
    emit("global/services/farm-mcp/config.yaml", json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    endpoints = json.loads(model["keys"]["APISERVER_ENDPOINT_IPS_JSON"])
    policy = {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": {
        "name": "farm-mcp-api-endpoints", "namespace": model["keys"]["NS_SYSTEM"]}, "spec": {
        "podSelector": {"matchLabels": {"app.kubernetes.io/name": "farm-mcp"}},
        "policyTypes": ["Egress"], "egress": [{"to": [{"ipBlock": {"cidr": ip + "/32"}}
            for ip in endpoints], "ports": [{"protocol": "TCP", "port": int(model["keys"]["APISERVER_PORT"])}]}]}}
    emit("global/services/farm-mcp/k8s/api-endpoints.yaml", json.dumps(policy, sort_keys=True, indent=2) + "\n")

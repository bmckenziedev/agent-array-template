"""Login provisioning configuration and API egress from the org model."""
import json

PLUGIN_NAME = "cluster-login-storage"


def render(model, emit):
    keys = model["keys"]
    nodes = [n for n in model["entities"]["node"] if n["NODE_IS_SESSION"] == "true"]
    config = {"nodePathMap": [{"node": "DEFAULT_PATH_FOR_NON_LISTED_NODES", "paths": []}]}
    config["nodePathMap"].extend({"node": n["NODE_NAME"], "paths": [keys["LOGIN_HOST_ROOT"]]}
                                 for n in sorted(nodes, key=lambda n: n["NODE_NAME"]))
    cm = {"apiVersion": "v1", "kind": "ConfigMap",
          "metadata": {"name": "login-local-path-config", "namespace": keys["NS_SYSTEM"]},
          "data": {"config.json": json.dumps(config, sort_keys=True)}}
    emit("global/cluster/k8s/login-storage/config.yaml", json.dumps(cm, indent=2) + "\n")
    endpoints = set(json.loads(keys["APISERVER_ENDPOINT_IPS_JSON"]))
    endpoints.add(keys["APISERVER_SERVICE_IP"])
    peers = [{"ipBlock": {"cidr": ip + ("/128" if ":" in ip else "/32")}}
             for ip in sorted(endpoints)]
    policy = {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
              "metadata": {"name": "login-local-path-api", "namespace": keys["NS_SYSTEM"]},
              "spec": {"podSelector": {"matchLabels": {"app.kubernetes.io/name": "login-local-path"}},
                       "policyTypes": ["Egress"], "egress": [{"to": peers,
                       "ports": [{"protocol": "TCP", "port": int(keys["APISERVER_PORT"])},
                                 {"protocol": "TCP", "port": 443}]}]}}
    emit("global/cluster/k8s/login-storage/api-egress.yaml", json.dumps(policy, indent=2) + "\n")

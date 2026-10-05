"""Deterministically allow every configured API-server endpoint, including post-DNAT."""

import ipaddress
import json

PLUGIN_NAME = "factory-apiserver-egress"


def render(model, emit):
    if not model["org"]["modules"]["factory"]["enabled"]:
        return
    keys = model["keys"]
    addresses = json.loads(keys["APISERVER_ENDPOINT_IPS_JSON"])
    addresses.append(keys["APISERVER_SERVICE_IP"])
    peers = [{"ipBlock": {"cidr": str(ipaddress.ip_address(address)) + "/" +
                         str(ipaddress.ip_address(address).max_prefixlen)}}
             for address in sorted(set(addresses))]
    manifest = {
        "apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
        "metadata": {"name": "factory-apiserver", "namespace": keys["NS_FACTORY"],
                     "labels": {"app.kubernetes.io/part-of": keys["PROJECT_NAME"]}},
        "spec": {"podSelector": {"matchLabels": {"app.kubernetes.io/name": "factory-api"}},
                 "policyTypes": ["Egress"], "egress": [{"to": peers, "ports": [
                     {"protocol": "TCP", "port": port}
                     for port in sorted({int(keys["APISERVER_PORT"]), 443})]}]},
    }
    emit("global/modules/factory/k8s/apiserver.yaml",
         json.dumps(manifest, sort_keys=True, indent=2) + "\n")

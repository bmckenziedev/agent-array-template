#!/usr/bin/env python3
"""Controller API/DNS egress under the kube-system namespace default-deny."""
import ipaddress
import json

PLUGIN_NAME = "sealed-secrets"


def render(model, emit):
    keys = model["keys"]
    endpoint_ips = json.loads(keys["APISERVER_ENDPOINT_IPS_JSON"])
    if not isinstance(endpoint_ips, list) or not endpoint_ips:
        raise ValueError("APISERVER_ENDPOINT_IPS_JSON must be a non-empty IP list")
    service_ip = keys["APISERVER_SERVICE_IP"]
    api_port = int(keys["APISERVER_PORT"])
    if not 1 <= api_port <= 65535:
        raise ValueError("APISERVER_PORT is invalid")
    endpoints = sorted({str(ipaddress.ip_address(ip)) for ip in endpoint_ips})
    service = str(ipaddress.ip_address(service_ip))

    def destinations(addresses):
        return [{"ipBlock": {"cidr": str(ipaddress.ip_network(address))}}
                for address in addresses]

    selector = {"app.kubernetes.io/name": "sealed-secrets",
                "app.kubernetes.io/instance": "sealed-secrets"}
    policy = {
        "apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
        "metadata": {"name": "sealed-secrets-controller-egress", "namespace": "kube-system",
                     "labels": {"app.kubernetes.io/part-of": keys["PROJECT_NAME"],
                                "app.kubernetes.io/component": "sealed-secrets"}},
        "spec": {"podSelector": {"matchLabels": selector}, "policyTypes": ["Egress"],
                 "egress": [
                     {"to": destinations(endpoints),
                      "ports": [{"protocol": "TCP", "port": api_port}]},
                     {"to": destinations([service]),
                      "ports": [{"protocol": "TCP", "port": 443}]},
                     {"to": [{"namespaceSelector": {"matchLabels": {
                         "kubernetes.io/metadata.name": "kube-system"}},
                         "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}}}],
                      "ports": [{"protocol": "UDP", "port": 53},
                                {"protocol": "TCP", "port": 53}]}]}}
    # JSON is valid YAML and avoids a third-party renderer dependency.
    comment = ("# kube-router evaluates endpoint egress after DNAT.\n"
               "# VERIFY Cilium/Calico service policy semantics and DNS pod labels.\n")
    emit("global/platform/sealed-secrets/k8s/controller-egress.yaml",
         comment + json.dumps(policy, indent=2, sort_keys=True) + "\n")

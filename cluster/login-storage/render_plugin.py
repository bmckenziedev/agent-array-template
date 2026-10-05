"""Isolation baseline for host-backed local-path login helpers."""
import json

PLUGIN_NAME = "login-storage-baseline"


def render(model, emit):
    keys = model["keys"]
    namespace = keys["PROJECT_NAME"] + "-login-storage"
    objects = [{"apiVersion": "v1", "kind": "Namespace", "metadata": {
        "name": namespace, "annotations": {
            "argocd.argoproj.io/sync-options": "Prune=false,Delete=false"},
        "labels": {"pod-security.kubernetes.io/enforce": "privileged",
                   "pod-security.kubernetes.io/enforce-version": "latest",
                   "pod-security.kubernetes.io/warn": "restricted",
                   "pod-security.kubernetes.io/warn-version": "latest",
                   "pod-security.kubernetes.io/audit": "restricted",
                   "pod-security.kubernetes.io/audit-version": "latest"}}}]
    for name, spec in (
        ("default-deny", {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]}),
        ("allow-dns", {"podSelector": {}, "policyTypes": ["Egress"], "egress": [{
            "to": [{"namespaceSelector": {"matchLabels": {
                "kubernetes.io/metadata.name": "kube-system"}},
                "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}}}],
            "ports": [{"protocol": p, "port": 53} for p in ("UDP", "TCP")]}]}),
    ):
        objects.append({"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
                        "metadata": {"name": name, "namespace": namespace}, "spec": spec})
    endpoints = json.loads(keys["APISERVER_ENDPOINT_IPS_JSON"])
    objects.append({"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
                    "metadata": {"name": "allow-apiserver", "namespace": namespace}, "spec": {
                        "podSelector": {}, "policyTypes": ["Egress"], "egress": [
                            {"to": [{"ipBlock": {"cidr": ip + ("/128" if ":" in ip else "/32")}}
                                    for ip in endpoints], "ports": [{"protocol": "TCP", "port": int(keys["APISERVER_PORT"])}]},
                            {"to": [{"ipBlock": {"cidr": keys["APISERVER_SERVICE_IP"] + "/32"}}],
                             "ports": [{"protocol": "TCP", "port": 443}]}]}})
    emit("global/cluster/login-storage/baseline.yaml",
         "# Local-path helper hostPath must be limited to LOGIN_HOST_ROOT by admission.\n" +
         "\n---\n".join(json.dumps(o, sort_keys=True, indent=2) for o in objects) + "\n")

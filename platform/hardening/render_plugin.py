"""Deterministic namespace security generation; Kubernetes JSON is valid YAML."""
import ipaddress
import json
from pathlib import Path
import re

PLUGIN_NAME = "hardening"
KEY_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")


def subst(text: str, keys: dict) -> str:
    def rep(match):
        if match.group(1) not in keys:
            raise KeyError(f"unknown placeholder {match.group(1)}")
        return str(keys[match.group(1)])
    return KEY_RE.sub(rep, text)


def defaults() -> dict:
    # This deliberately reads the documented YAML subset without a dependency.
    text = (Path(__file__).parent / "org.component.defaults.yaml").read_text()
    result = {"psa": {}}
    in_psa = False
    for line in text.splitlines():
        if line == "  psa:":
            in_psa = True
        elif line.startswith("    ") and in_psa:
            key, value = line.strip().split(":", 1)
            result["psa"][key] = value.strip()
        elif line.startswith("  ") and not line.startswith(("    ", "  - ")):
            in_psa = False
            key, value = line.strip().split(":", 1)
            if key == "psa_version":
                result[key] = value.strip().strip('"')
            if key == "apiserver_clients":
                result[key] = []
        elif line.startswith("  - "):
            result["apiserver_clients"].append(line[4:])
    return result


def policy(namespace: str, name: str, spec: dict) -> dict:
    return {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
            "metadata": {"name": name, "namespace": namespace}, "spec": spec}


def render(model: dict, emit) -> None:
    settings = defaults()
    override = model["org"].get("components", {}).get("hardening", {})
    settings.update({k: v for k, v in override.items() if k != "psa"})
    settings["psa"].update(override.get("psa", {}))
    namespaces = {k: v for k, v in model["org"]["namespaces"].items()
                  if k != "user_prefix"}
    for name in ("kube-system", "kube-public", "kube-node-lease", "default"):
        namespaces[name] = name
    if len(set(namespaces.values())) != len(namespaces):
        raise ValueError("namespace names must be unique")
    unknown = set(settings["apiserver_clients"]) - set(namespaces)
    if unknown:
        raise ValueError(f"unknown apiserver client refs: {sorted(unknown)}")
    keys = model["keys"]
    endpoints = sorted(set(json.loads(keys["APISERVER_ENDPOINT_IPS_JSON"])))
    port = int(keys["APISERVER_PORT"])
    if not endpoints or not 1 <= port <= 65535:
        raise ValueError("valid API endpoints and TCP port are required")
    def peer(ip):
        address = ipaddress.ip_address(ip)
        return {"ipBlock": {"cidr": f"{address}/{address.max_prefixlen}"}}
    for ref, name in sorted(namespaces.items()):
        level = settings["psa"].get(ref)
        if level not in ("restricted", "baseline", "privileged"):
            raise ValueError(f"missing or invalid PSA level for {ref}")
        version = settings["psa_version"]
        if not re.fullmatch(r"latest|v1\.\d+", version):
            raise ValueError("invalid PSA version")
        labels = {"pod-security.kubernetes.io/enforce": level,
                  "pod-security.kubernetes.io/enforce-version": version,
                  keys["LABEL_PREFIX"] + "/baseline": "true"}
        for mode in ("warn", "audit"):
            labels[f"pod-security.kubernetes.io/{mode}"] = "baseline" if ref == "kube-system" else "restricted"
            labels[f"pod-security.kubernetes.io/{mode}-version"] = version
        objects = [{"apiVersion": "v1", "kind": "Namespace",
                    "metadata": {"name": name, "labels": labels, "annotations": {
                        "argocd.argoproj.io/sync-options": "Prune=false,Delete=false"}}},
                   policy(name, "default-deny", {"podSelector": {},
                          "policyTypes": ["Ingress", "Egress"]})]
        if ref in settings["apiserver_clients"] or ref == "kube-system":
            objects.append(policy(name, "allow-apiserver-endpoint", {
                "podSelector": {}, "policyTypes": ["Egress"], "egress": [
                    {"to": [peer(ip) for ip in endpoints],
                     "ports": [{"protocol": "TCP", "port": port}]},
                    {"to": [peer(keys["APISERVER_SERVICE_IP"])],
                     "ports": [{"protocol": "TCP", "port": 443}]}]}))
        objects.append(policy(name, "allow-cluster-dns", {
            "podSelector": {}, "policyTypes": ["Egress"], "egress": [{
                "to": [{"namespaceSelector": {"matchLabels": {
                    "kubernetes.io/metadata.name": "kube-system"}},
                    "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}}},
                       peer(keys["CLUSTER_DNS_IP"])],
                "ports": [{"protocol": p, "port": 53} for p in ("UDP", "TCP")]}]}))
        scrape = {"namespaceSelector": {"matchLabels": {
            "kubernetes.io/metadata.name": keys["NS_MONITORING"]}},
            "podSelector": {"matchLabels": {"app.kubernetes.io/name": "prometheus"}}}
        objects.append(policy(name, "allow-monitoring-scrape", {
            "podSelector": {}, "policyTypes": ["Ingress"], "ingress": [{"from": [scrape], "ports": [{"protocol": "TCP"}]}]}))
        if ref in ("kube-system", "argocd", "monitoring"):
            ingress = [{"from": [{"podSelector": {}}]}, {"from": [scrape]}]
            if ref in ("argocd", "monitoring"):
                ingress.append({"from": [{"namespaceSelector": {"matchLabels": {
                    "kubernetes.io/metadata.name": keys["NS_PORTAL"]}}}]})
            objects.append(policy(name, "allow-platform", {
                "podSelector": {}, "policyTypes": ["Ingress", "Egress"],
                "ingress": ingress, "egress": [{"to": [{"podSelector": {}}]}]}))
        if ref == "kube-system":
            objects.append(policy(name, "allow-coredns-ingress", {
                "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
                "policyTypes": ["Ingress"], "ingress": [{"from": [{"namespaceSelector": {}}],
                    "ports": [{"protocol": p, "port": 53} for p in ("UDP", "TCP")]}]}))
            objects.append(policy(name, "allow-upstream", {
                "podSelector": {}, "policyTypes": ["Egress"], "egress": [{
                    "to": [{"ipBlock": {"cidr": "0.0.0.0/0"}},
                           {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}},
                            "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}}}],
                    "ports": [{"protocol": "UDP", "port": 53},
                              {"protocol": "TCP", "port": 53}, {"protocol": "TCP", "port": 443}]}]}))
        if ref == "monitoring":
            node_ips = sorted({ip for n in model["org"]["nodes"]
                               for ip in (n.get("overlay_ip"), n.get("public_ip"), n.get("lan_ip")) if ip})
            objects.append(policy(name, "prometheus-scrape-egress", {
                "podSelector": {"matchLabels": {"app.kubernetes.io/name": "prometheus"}},
                "policyTypes": ["Egress"], "egress": [
                    {"to": [{"namespaceSelector": {}}]},
                    {"to": [peer(ip) for ip in node_ips],
                     "ports": [{"protocol": "TCP", "port": p} for p in (10250, 9100)]}]}))
            for app in ("grafana", "alertmanager"):
                objects.append(policy(name, app + "-public-https", {
                    "podSelector": {"matchLabels": {"app.kubernetes.io/name": app}},
                    "policyTypes": ["Egress"], "egress": [{"to": [{"ipBlock": {
                        "cidr": "0.0.0.0/0", "except": json.loads(keys["PRIVATE_CIDRS_JSON"])}}],
                        "ports": [{"protocol": "TCP", "port": 443}]}]}))
        if keys["CNI"] == "cilium" and (ref in settings["apiserver_clients"] or ref == "kube-system"):
            objects.append({"apiVersion": "cilium.io/v2", "kind": "CiliumNetworkPolicy",
                "metadata": {"name": "allow-apiserver-entity", "namespace": name},
                "spec": {"endpointSelector": {}, "egress": [{"toEntities": ["kube-apiserver"]}]}})
        note = ("# kube-router/Calico iptables evaluate after DNAT: endpoint + service are intentional.\n"
                "# Keep endpoint addresses synced to kubectl get endpoints kubernetes.\n")
        emit(f"global/platform/hardening/k8s/namespace-{name}.yaml",
             note + "\n---\n".join(json.dumps(o, indent=2, sort_keys=True)
                                     for o in objects) + "\n")

    extras = [keys["PROJECT_NAME"] + "-login-storage"]
    modules = model["org"].get("modules", {})
    if modules.get("arc-ci", {}).get("enabled"):
        extras += [keys["PROJECT_NAME"] + "-arc-" + suffix for suffix in ("systems", "runners", "heavy")]
    if modules.get("wazuh", {}).get("enabled"):
        extras.append(keys["PROJECT_NAME"] + "-wazuh")
    for namespace in extras:
        obj = policy(namespace, "allow-monitoring-scrape", {"podSelector": {}, "policyTypes": ["Ingress"],
            "ingress": [{"from": [{"namespaceSelector": {"matchLabels": {
                "kubernetes.io/metadata.name": keys["NS_MONITORING"]}},
                "podSelector": {"matchLabels": {"app.kubernetes.io/name": "prometheus"}}}],
                "ports": [{"protocol": "TCP"}]}]})
        emit("global/platform/hardening/k8s/scrape-" + namespace + ".yaml", json.dumps(obj, sort_keys=True, indent=2) + "\n")

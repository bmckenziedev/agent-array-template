"""Emit mirror policies; DNS and configured upstream CIDRs are the only egress."""
import ipaddress
import json
from urllib.parse import urlsplit

PLUGIN_NAME = "pkg-mirror-policy"


def render(model, emit):
    org = model["org"]
    cfg = org.get("modules", {}).get("pkg-mirror", {})
    if not cfg.get("enabled", False):
        return
    for field in ("npm_upstream", "pypi_upstream"):
        value = cfg.get(field, "https://registry.npmjs.org/" if field == "npm_upstream" else "https://pypi.org/simple/")
        url = urlsplit(value)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError(f"{field} must be an HTTPS URL without credentials, query or fragment")
    namespace = org["project"]["name"] + "-pkg-mirror"
    namespaces = org["namespaces"]
    def policy(name, selector, kind, rules):
        obj = {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
               "metadata": {"name": name, "namespace": namespace},
               "spec": {"podSelector": {"matchLabels": selector}, "policyTypes": [kind], kind.lower(): rules}}
        emit("global/modules/pkg-mirror/k8s/" + name + ".yaml", json.dumps(obj, sort_keys=True, indent=2) + "\n")
    for app, port in (("npm", 4873), ("pypi", 3141)):
        policy(app + "-ingress", {"app.kubernetes.io/name": app}, "Ingress", [{"from": [{
            "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": namespaces["session_jobs"]}},
            "podSelector": {"matchLabels": {"app.kubernetes.io/name": "session-job"}}}],
            "ports": [{"protocol": "TCP", "port": port}]}])
    rules = [{"to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}},
                       "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}}}],
              "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}]}]
    denied = {"10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10",
              "169.254.0.0/16", "127.0.0.0/8", "0.0.0.0/8", "224.0.0.0/4", "240.0.0.0/4"}
    denied.update(json.loads(model["keys"]["SESSION_EGRESS_DENY_CIDRS_JSON"]))
    for cidr in cfg.get("upstream_cidrs", ["0.0.0.0/0"]):
        network = ipaddress.ip_network(cidr)
        block = {"cidr": str(network)}
        exclusions = sorted(c for c in denied if ipaddress.ip_network(c).subnet_of(network))
        if exclusions:
            block["except"] = exclusions
        rules.append({"to": [{"ipBlock": block}], "ports": [{"protocol": "TCP", "port": 443}]})
    policy("mirror-egress", {"app.kubernetes.io/part-of": org["project"]["name"]}, "Egress", rules)

"""Deterministic per-user policy and exact CIDR egress."""
import copy
import hashlib
import ipaddress
import json
import re
from urllib.parse import urlsplit
from pathlib import Path

PLUGIN_NAME = "supervisor"
RANK = {"auto": 0, "policy": 1, "human": 2}


def resolve(config, teams):
    result = copy.deepcopy(config)
    variants = []
    for team in teams or [None]:
        v = copy.deepcopy(config)
        override = config.get("team_policies", {}).get(team, {})
        for key, value in override.items():
            if key in {"permission_tiers", "deciders"}:
                v[key].update(value)
            else:
                v[key] = value
        variants.append(v)
    for category in config["permission_tiers"]:
        result["permission_tiers"][category] = max(
            (v["permission_tiers"][category] for v in variants), key=RANK.__getitem__)
        result["deciders"][category] = sorted(set.intersection(
            *(set(v["deciders"].get(category, [])) for v in variants)))
    result["policy_may_allow"] = sorted(set.intersection(
        *(set(v["policy_may_allow"]) for v in variants)))
    for key in ["human_timeout_s", "policy_hook_timeout_s"]:
        result[key] = min(v[key] for v in variants)
    result.pop("team_policies", None)
    return result


def render(model, emit):
    keys = model["keys"]
    config = model["org"]["components"]["supervisor"]
    names = json.loads((Path(__file__).parent / "aa_supervisor/secret_key_names.json").read_text())
    secret_names = sorted({re.sub(r"\{\{([A-Z][A-Z0-9_]*)\}\}", lambda m: keys[m[1]], n) for n in names})
    for url in [config["console_url"], config["policy_hook_url"],
                *(c.get("url", "") for c in config["notifiers"] + config["work_item_adapters"])]:
        if not url:
            continue
        parsed = urlsplit(url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("supervisor receivers require credential-free HTTPS URLs")
    for entry in config["notifiers"] + config["work_item_adapters"]:
        if any(re.search(r"secret|password|credential|token", key, re.I) for key in entry):
            raise ValueError("delivery configuration cannot hold credentials")
    if config["policy_hook_url"] or any(c.get("type") == "webhook" for c in config["notifiers"] + config["work_item_adapters"]):
        if not config["hook_egress"]:
            raise ValueError("hook receivers need explicit hook_egress")
    peers = [(cidr, config["console_port"]) for cidr in config["console_cidrs"]]
    peers += [(v["cidr"], v["port"]) for v in config["hook_egress"]]
    endpoints = json.loads(keys["APISERVER_ENDPOINT_IPS_JSON"]) + [keys["APISERVER_SERVICE_IP"]]
    for cidr, port in peers:
        network = ipaddress.ip_network(cidr, strict=True)
        if network.prefixlen == 0 or any(ipaddress.ip_address(ip) in network for ip in endpoints):
            raise ValueError("supervisor egress includes control plane or unrestricted CIDR")
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError("invalid egress port")
    teams = {t["id"]: t for t in model["teams"]}
    active = {u["slug"]: u for u in model["users"] if u["status"] == "active"}
    for user in sorted(model["users"], key=lambda u: u["slug"]):
        if user["status"] == "offboarded":
            continue
        policy = resolve(config, user["teams"])
        policy["secret_key_names"] = secret_names
        leads = set().union(*(set(teams[t]["leads"]) for t in user["teams"]))
        wanted = leads | set().union(*(set(v) for v in policy["deciders"].values()))
        policy.update(holder={k: user[k] for k in ["slug", "oidc_sub", "status", "teams", "primary_team"]},
                      known_users=[{k: active[s][k] for k in ["slug", "oidc_sub", "status"]} for s in sorted(active)],
                      principals=[{k: active[s][k] for k in ["slug", "oidc_sub", "status"]}
                                  for s in sorted(wanted & active.keys())],
                      leads=sorted(leads & active.keys()), breakglass_group=keys["GROUP_BREAKGLASS"])
        policy.pop("image", None)
        data = json.dumps(policy, sort_keys=True, separators=(",", ":"))
        metadata = {"name": "supervisor-policy", "namespace": keys["USER_NS_PREFIX"] + user["slug"],
                    "labels": {"app.kubernetes.io/name": "supervisor-policy",
                               "app.kubernetes.io/part-of": keys["PROJECT_NAME"],
                               "app.kubernetes.io/component": "supervisor",
                               keys["LABEL_PREFIX"] + "/user": user["slug"]}}
        prefix = f"users/{user['slug']}/services/supervisor/"
        emit(prefix + "supervisor-policy.yaml", json.dumps({"apiVersion": "v1", "kind": "ConfigMap",
             "metadata": metadata, "data": {"policy.json": data,
             "policy.sha256": hashlib.sha256(data.encode()).hexdigest()}}, sort_keys=True) + "\n")
        if peers:
            emit(prefix + "netpol-supervisor-egress.yaml", json.dumps({
                "apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
                "metadata": dict(metadata, name="supervisor-egress", labels={**metadata["labels"],
                    "app.kubernetes.io/name": "supervisor-egress"}), "spec": {
                    "podSelector": {"matchLabels": {keys["LABEL_PREFIX"] + "/user": user["slug"]}},
                    "policyTypes": ["Egress"], "egress": [{"to": [{"ipBlock": {"cidr": cidr}}],
                    "ports": [{"protocol": "TCP", "port": port}]} for cidr, port in sorted(peers)]}},
                sort_keys=True) + "\n")

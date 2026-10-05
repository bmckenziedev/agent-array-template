"""Emit conditional vendor egress and cluster-scoped, per-user login PVs."""
import re
import json
import ipaddress

PLUGIN_NAME = "sessions-storage-egress"
PV_SOURCE = """apiVersion: v1
kind: PersistentVolume
metadata:
  name: '{{USER_NS}}-{{TOOL_HOME_CLAIM}}'
  labels:
    app.kubernetes.io/name: '{{USER_NS}}-{{TOOL_HOME_CLAIM}}'
    app.kubernetes.io/part-of: '{{PROJECT_NAME}}'
    app.kubernetes.io/component: sessions
    '{{LABEL_PREFIX}}/user': '{{USER_SLUG}}'
    '{{LABEL_PREFIX}}/team': '{{USER_PRIMARY_TEAM}}'
    '{{LABEL_PREFIX}}/tool': '{{TOOL}}'
    '{{LABEL_PREFIX}}/account': '{{TOOL_ACCOUNT_ID}}'
    '{{LABEL_PREFIX}}/kind': user-login
spec:
  capacity:
    storage: 4Gi
  accessModes:
  - ReadWriteOnce
  persistentVolumeReclaimPolicy: Retain
  storageClassName: '{{STORAGE_CLASS_LOGIN}}'
  claimRef:
    namespace: '{{USER_NS}}'
    name: '{{TOOL_HOME_CLAIM}}'
  local:
    path: '{{LOGIN_HOST_ROOT}}/{{USER_SLUG}}/{{TOOL}}/{{TOOL_HOME_NODE}}'
  nodeAffinity:
    required:
      nodeSelectorTerms:
      - matchExpressions:
        - key: kubernetes.io/hostname
          operator: In
          values:
          - '{{TOOL_HOME_NODE}}'
"""

KEY_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")

def subst(text, keys):
    def replace(match):
        name = match.group(1)
        if name not in keys:
            raise KeyError("unknown placeholder " + name)
        return str(keys[name])
    return KEY_RE.sub(replace, text)

def render(model, emit):
    # Include every API endpoint. CNIs may evaluate the service rule after DNAT.
    keys = model["keys"]
    addresses = sorted(set(json.loads(keys["APISERVER_ENDPOINT_IPS_JSON"])) |
                       {n["overlay_ip"] for n in model["org"]["nodes"] if "control-plane" in n["roles"]})
    peers = []
    for address in addresses:
        value = str(ipaddress.ip_address(address))
        peers.append({"ipBlock": {"cidr": value + ("/128" if ":" in value else "/32")}})
    api_policy = {
        "apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
        "metadata": {"name": "session-admission-api-endpoints", "namespace": keys["NS_SYSTEM"]},
        "spec": {"podSelector": {"matchLabels": {"app.kubernetes.io/name": "session-admission"}},
                 "policyTypes": ["Ingress", "Egress"],
                 "ingress": [{"from": peers, "ports": [{"protocol": "TCP", "port": 8443}]}],
                 "egress": [{"to": peers, "ports": [{"protocol": "TCP", "port": int(keys["APISERVER_PORT"])}]}]},
    }
    emit("global/sessions/k8s/session-admission-api-endpoints.yaml",
         json.dumps(api_policy, indent=2, sort_keys=True) + "\n")
    if model["org"]["vendors"]["moonshot"]["enabled"]:
        config = {"apiVersion": "v1", "kind": "ConfigMap",
                  "metadata": {"name": "kimi-egress-denied-cidrs", "namespace": keys["NS_EGRESS"]},
                  "data": {"denied-cidrs.json": keys["SESSION_EGRESS_DENY_CIDRS_JSON"]}}
        emit("global/sessions/kimi/k8s/egress-denied-cidrs.yaml", json.dumps(config, indent=2, sort_keys=True) + "\n")
    for entity in sorted(model["entities"]["user_tool"], key=lambda e: e["ENTITY_ID"]):
        tool = entity["TOOL"]
        if tool == "kimi" and not model["org"]["vendors"]["moonshot"]["enabled"]:
            continue
        keys = {**model["keys"], **entity}
        denied = json.loads(keys["SESSION_EGRESS_DENY_CIDRS_JSON"])
        keys["SESSION_EGRESS_DENY_CIDRS_JSON"] = json.dumps(sorted(set(denied + ["0.0.0.0/8", "224.0.0.0/4", "240.0.0.0/4"])))
        text = subst(PV_SOURCE, keys)
        emit("global/sessions/k8s/login-pv-" + entity["USER_SLUG"] + "-" + tool + ".yaml", text)
        mode = keys[tool.upper() + "_EGRESS"]
        if mode == "public-443":
            text = """apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: vendor-egress-{{TOOL}}
  namespace: "{{USER_NS}}"
spec:
  podSelector:
    matchLabels:
      "{{LABEL_PREFIX}}/user": "{{USER_SLUG}}"
      "{{LABEL_PREFIX}}/tool": "{{TOOL}}"
  policyTypes: [Egress]
  egress:
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0
            except: {{SESSION_EGRESS_DENY_CIDRS_JSON}}
      ports:
        - protocol: TCP
          port: 443
"""
        elif tool == "kimi":
            text = """apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: vendor-egress-kimi
  namespace: "{{USER_NS}}"
spec:
  podSelector:
    matchLabels:
      "{{LABEL_PREFIX}}/user": "{{USER_SLUG}}"
      "{{LABEL_PREFIX}}/tool": kimi
  policyTypes: [Egress]
  egress:
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: "{{NS_EGRESS}}"
          podSelector:
            matchLabels:
              app.kubernetes.io/name: kimi-egress
      ports:
        - protocol: TCP
          port: 3128
"""
        else:
            continue
        emit("users/" + entity["USER_SLUG"] + "/sessions/vendor-egress-" + tool + ".yaml", subst(text, keys))

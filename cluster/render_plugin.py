"""Login provisioning configuration and API egress from the org model."""
import json
import shlex

PLUGIN_NAME = "cluster-login-storage"


def render(model, emit):
    keys = model["keys"]
    nodes = [n for n in model["entities"]["node"] if n["NODE_IS_SESSION"] == "true"]
    config = {"nodePathMap": [{"node": "DEFAULT_PATH_FOR_NON_LISTED_NODES", "paths": []}]}
    config["nodePathMap"].extend({"node": n["NODE_NAME"], "paths": [keys["LOGIN_HOST_ROOT"]]}
                                 for n in sorted(nodes, key=lambda n: n["NODE_NAME"]))
    cm = {"apiVersion": "v1", "kind": "ConfigMap",
          "metadata": {"name": "login-local-path-config", "namespace": keys["PROJECT_NAME"] + "-login-storage"},
          "data": {"config.json": json.dumps(config, sort_keys=True)}}
    emit("global/cluster/k8s/login-storage/config.yaml", json.dumps(cm, indent=2) + "\n")
    endpoints = set(json.loads(keys["APISERVER_ENDPOINT_IPS_JSON"]))
    endpoints.add(keys["APISERVER_SERVICE_IP"])
    peers = [{"ipBlock": {"cidr": ip + ("/128" if ":" in ip else "/32")}}
             for ip in sorted(endpoints)]
    policy = {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
              "metadata": {"name": "login-local-path-api", "namespace": keys["PROJECT_NAME"] + "-login-storage"},
              "spec": {"podSelector": {"matchLabels": {"app.kubernetes.io/name": "login-local-path"}},
                       "policyTypes": ["Egress"], "egress": [{"to": peers,
                       "ports": [{"protocol": "TCP", "port": int(keys["APISERVER_PORT"])},
                                 {"protocol": "TCP", "port": 443}]}]}}
    emit("global/cluster/k8s/login-storage/api-egress.yaml", json.dumps(policy, indent=2) + "\n")
    # Pre-bound local PVs do not invoke the dynamic provisioner's setup hook.
    # Stage their directories explicitly on the declared home node before pods start.
    for node in sorted(nodes, key=lambda n: n["NODE_NAME"]):
        homes = sorted({(e["USER_SLUG"], e["TOOL"], e["TOOL_HOME_NODE"])
                        for e in model["entities"]["user_tool"]
                        if e["TOOL_HOME_NODE"] == node["NODE_NAME"]})
        paths = [(keys["LOGIN_HOST_ROOT"], "root", "0711")]
        for user, tool, home in homes:
            parent = keys["LOGIN_HOST_ROOT"] + "/" + user
            login = parent + "/" + tool + "/" + home
            paths.extend([(parent, "root", "0711"), (parent + "/" + tool, "root", "0711"),
                          (login, "1000", "0700"), (login + "/projects", "1000", "0700"),
                          (login + "/sessions", "1000", "0700")])
        script = """#!/usr/bin/env bash
set -euo pipefail
apply=false
case "${1:-}" in
  "") ;;
  --yes) apply=true ;;
  *) echo 'usage: prepare-login-homes.sh [--yes]' >&2; exit 2 ;;
esac
if $apply; then
  [[ $(id -u) == 0 ]] || { echo 'root required' >&2; exit 1; }
fi
prepare() {
  local path=$1 owner=$2 mode=$3 probe=$1
  # Refuse symlinks in every ancestor, including the login root itself.
  while [[ "$probe" != / ]]; do
    [[ ! -L "$probe" ]] || { echo 'symlink login path refused' >&2; exit 1; }
    probe=$(dirname "$probe")
  done
  printf 'install -d -o %s -g %s -m %s %q\\n' "$owner" "$owner" "$mode" "$path"
  if $apply; then install -d -o "$owner" -g "$owner" -m "$mode" "$path"; fi
}
"""
        script += "# Run only on " + node["NODE_NAME"] + " after encrypted login-root preparation.\n"
        script += "if $apply; then\n  [[ $(hostname) == " + shlex.quote(node["NODE_NAME"]) + \
                  " ]] || { echo 'home node mismatch' >&2; exit 1; }\nfi\n"
        for path, owner, mode in sorted(set(paths), key=lambda item: (item[0].count("/"), item[0])):
            script += "prepare " + shlex.quote(path) + " " + owner + " " + mode + "\n"
        emit("files/cluster/node-prep/" + node["NODE_NAME"] + "/prepare-login-homes.sh", script)

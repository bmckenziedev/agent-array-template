"""Expose computed identity fields without storing them in laptop config."""

import json

PLUGIN_NAME = "aa-client-directory"


def render(model, emit):
    users = {u["slug"]: u for u in model["users"]}
    identities = {}
    for entity in sorted(model["entities"]["user"], key=lambda e: e["USER_SLUG"]):
        user = users[entity["USER_SLUG"]]
        identities[entity["USER_OIDC_SUBJECT"]] = {
            "slug": user["slug"], "oidc_sub": user["oidc_sub"],
            "namespace": entity["USER_NS"],
            "max_replicas_per_tool": int(entity["TIER_MAX_REPLICAS_PER_TOOL"]),
        }
    keys = model["keys"]
    cm = {
        "apiVersion": "v1", "kind": "ConfigMap",
        "metadata": {"name": "aa-client-directory", "namespace": keys["NS_SYSTEM"],
                     "labels": {"app.kubernetes.io/name": "aa",
                                "app.kubernetes.io/part-of": keys["PROJECT_NAME"],
                                "app.kubernetes.io/component": "user-client"}},
        "data": {"identities.json": json.dumps(identities, sort_keys=True),
                 "username-prefix.json": json.dumps(keys["OIDC_USERNAME_PREFIX"])},
    }
    emit("global/tools/aa/k8s/client-directory.yaml", json.dumps(cm, indent=2, sort_keys=True) + "\n")

"""Enforce suspension independently of user-controlled StatefulSet replicas."""
import json

PLUGIN_NAME = "sessions-suspend"


def render(model, emit):
    for user in sorted(model["entities"]["user"], key=lambda u: u["USER_SLUG"]):
        if user["USER_STATUS"] != "suspended":
            continue
        quota = {"apiVersion": "v1", "kind": "ResourceQuota",
                 "metadata": {"name": "suspended", "namespace": user["USER_NS"]},
                 "spec": {"hard": {"pods": "0"}}}
        emit(f"users/{user['USER_SLUG']}/sessions/suspend/quota.yaml",
             json.dumps(quota, sort_keys=True, indent=2) + "\n")

#!/usr/bin/env python3
"""Synthetic kubectl transport; never accesses a cluster."""

import json
import os
import sys
from pathlib import Path


def main():
    state = json.loads(Path(os.environ["AA_FAKE_STATE"]).read_text())
    args = sys.argv[1:]
    with Path(os.environ["AA_FAKE_CALLS"]).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(args) + "\n")
    if args == ["oidc-login", "--help"]:
        return 0 if state.get("plugin", True) else 1
    if "--kubeconfig" not in args or "--context" not in args:
        return 2
    args = args[4:]
    namespace = None
    if args[:1] == ["-n"]:
        namespace, args = args[1], args[2:]
    if args[:2] == ["auth", "whoami"]:
        result = {"status": {"userInfo": {"username": state["subject"]}}}
    elif args[:2] == ["get", "configmap"]:
        name = args[2]
        result = {"data": state["configmaps"][name]}
    elif args[:2] == ["get", "namespace"]:
        result = state["namespace"]
    elif args[:2] == ["get", "--raw"]:
        result = state["pace"]
    elif args[0] == "get" and args[1] in ("pods", "statefulsets"):
        selector = args[args.index("-l") + 1]
        wanted = dict(p.split("=", 1) for p in selector.split(","))
        items = [i for i in state[args[1]] if all(i["metadata"]["labels"].get(k) == v
                 for k, v in wanted.items())]
        result = {"items": items}
    elif args[0] in ("scale", "exec", "attach", "logs"):
        if namespace != state["namespace"]["metadata"]["name"]:
            return 3
        result = {"ok": True}
    else:
        return 4
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

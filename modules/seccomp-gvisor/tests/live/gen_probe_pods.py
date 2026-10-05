#!/usr/bin/env python3
"""Emit diagnostic manifests only; never contact Kubernetes.

Explicit digest-pinned image, runtime names, label prefix and role are required.
Probe namespace and pods must be reviewed before any live installation.
"""
import argparse
import json
import pathlib
import sys

SHIPPED = "profiles/seccomp-gvisor/runtime-default-clone3-enosys.json"
IMAGES = {}
NODES = {}
RUNTIMES = {}


def namespace_objects(ns):
    here = pathlib.Path(__file__).resolve().parent
    files = {n: (here / n).read_text(encoding="utf-8").replace("\r\n", "\n")
             for n in ("probe.sh", "escape.py", "sweep.py")}
    return [
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": ns, "labels": {
            "app.kubernetes.io/component": "seccomp-probe",
            "pod-security.kubernetes.io/enforce": "baseline",
            "pod-security.kubernetes.io/enforce-version": "latest",
            # warn/audit only: shows that PSA restricted accepts the Localhost profile
            "pod-security.kubernetes.io/warn": "restricted",
            "pod-security.kubernetes.io/audit": "restricted"}}},
        # the probes need no network at all
        {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
         "metadata": {"name": "deny-all", "namespace": ns},
         "spec": {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]}},
        {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "probe", "namespace": ns},
         "data": files},
    ]


def seccomp(kind):
    if kind == "rd":
        return {"type": "RuntimeDefault"}
    if kind == "unconfined":
        return {"type": "Unconfined"}
    if kind == "local":
        return {"type": "Localhost", "localhostProfile": SHIPPED}
    if kind.startswith("local="):
        return {"type": "Localhost", "localhostProfile": kind[len("local="):]}
    raise SystemExit("bad SECCOMP %r" % kind)


COMMANDS = {"probe": "bash /probe/probe.sh", "escape": "python3 /probe/escape.py", "sweep": "python3 /probe/sweep.py"}


def pod(ns, name, runtime, sc, image, node, script="probe"):
    # Same hardening as the session Job's containers (non-root, no caps, no privilege
    # escalation, read-only root): the seccomp profile is the only variable.
    spec = {
        "restartPolicy": "Never",
        "automountServiceAccountToken": False,
        "enableServiceLinks": False,
        "nodeSelector": NODES[node],
        "securityContext": {"runAsNonRoot": True, "runAsUser": 1000, "runAsGroup": 1000,
                            "seccompProfile": seccomp(sc)},
        "containers": [{
            "name": "probe", "image": IMAGES[image], "imagePullPolicy": "IfNotPresent",
            # head caps a runaway script's log (an unbounded log once rotated the evidence away)
            "command": ["sh", "-c", "%s 2>&1 | head -n 2000; echo PROBE_DONE; sleep 900" % COMMANDS[script]],
            "env": [{"name": "HOME", "value": "/tmp"}],
            "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                "capabilities": {"drop": ["ALL"]}},
            "resources": {"requests": {"cpu": "100m", "memory": "128Mi"},
                          "limits": {"cpu": "1", "memory": "768Mi"}},
            "volumeMounts": [{"name": "probe", "mountPath": "/probe", "readOnly": True},
                             {"name": "tmp", "mountPath": "/tmp"}]}],
        "volumes": [{"name": "probe", "configMap": {"name": "probe", "defaultMode": 0o555}},
                    {"name": "tmp", "emptyDir": {"medium": "Memory", "sizeLimit": "128Mi"}}],
    }
    if runtime != "runc":
        spec["runtimeClassName"] = RUNTIMES[runtime]
    return {"apiVersion": "v1", "kind": "Pod",
            "metadata": {"name": name, "namespace": ns, "labels": {"app": "seccomp-probe"}},
            "spec": spec}


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--label-prefix", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--runtime-gvisor", required=True)
    parser.add_argument("--runtime-vm", required=True)
    parser.add_argument("--script", choices=COMMANDS, default="probe")
    args = parser.parse_args(argv)
    image = args.image
    if "@sha256:" not in image or len(image.rsplit("@sha256:", 1)[1]) != 64:
        parser.error("diagnostic image must be SHA256 digest-pinned")
    IMAGES["probe"] = image
    NODES["target"] = {args.label_prefix + "/role-" + args.role: "true"}
    RUNTIMES.update(gvisor=args.runtime_gvisor, kata=args.runtime_vm)
    items = namespace_objects(args.namespace)
    for name, runtime, profile in (("gvisor-local", "gvisor", "local"),
                                    ("gvisor-control", "gvisor", "rd"),
                                    ("runc-local", "runc", "local")):
        items.append(pod(args.namespace, name, runtime, profile, "probe", "target", args.script))
    print(json.dumps({"apiVersion": "v1", "kind": "List", "items": items}, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])

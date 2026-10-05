#!/usr/bin/env python3
"""Opt-in server dry-runs against installed, rendered admission controls.

Exit 0: all expectations matched; exit 1: mismatch; exit 2: CLI/input error.
Nothing is persisted. Requires impersonation rights and already-synced policies.
"""
import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys
import yaml


def cases(namespace: str):
    pod = {"apiVersion": "v1", "kind": "Pod",
           "metadata": {"name": "admission-check", "namespace": namespace},
           "spec": {"containers": [{"name": "busybox", "image": "busybox:1.36"}]}}
    deployment = {"apiVersion": "apps/v1", "kind": "Deployment",
                  "metadata": copy.deepcopy(pod["metadata"]), "spec": {
                      "selector": {"matchLabels": {"app": "admission-check"}},
                      "template": {"metadata": {"labels": {"app": "admission-check"}},
                                   "spec": copy.deepcopy(pod["spec"])}}}
    positives = [("plain Pod", pod), ("plain Deployment", deployment)]
    service = {"apiVersion": "v1", "kind": "Service",
               "metadata": copy.deepcopy(pod["metadata"]),
               "spec": {"ports": [{"port": 80, "targetPort": 80}]}}
    negatives = []
    for typ in ("NodePort", "LoadBalancer"):
        obj = copy.deepcopy(service)
        obj["spec"]["type"] = typ
        negatives.append((typ, obj))
    obj = copy.deepcopy(service)
    obj["spec"]["externalIPs"] = ["203.0.113.20"]
    negatives.append(("externalIPs", obj))
    for field, value in (("hostPID", True), ("hostIPC", True), ("hostNetwork", True),
                         ("nodeName", "node-b"),
                         ("volumes", [{"name": "root", "hostPath": {"path": "/"}}])):
        for base in (pod, deployment):
            obj = copy.deepcopy(base)
            spec = obj["spec"] if obj["kind"] == "Pod" else obj["spec"]["template"]["spec"]
            spec[field] = value
            negatives.append((obj["kind"] + " " + field, obj))
    for field in ("containers", "initContainers", "ephemeralContainers"):
        for change in ({"ports": [{"containerPort": 80, "hostPort": 8080}]},
                       {"securityContext": {"privileged": True}},
                       {"securityContext": {"procMount": "Unmasked"}},
                       {"securityContext": {"capabilities": {"add": ["SYS_ADMIN"]}}}):
            obj = copy.deepcopy(pod)
            obj["spec"][field] = [{"name": "bad", "image": "busybox:1.36", **change}]
            # Ephemeral containers must use their subresource, not Pod CREATE.
            if field != "ephemeralContainers":
                negatives.append((field + " " + next(iter(change)), obj))
    obj = copy.deepcopy(pod)
    obj["metadata"].update(name="kps-prometheus-node-exporter-forged", ownerReferences=[{
        "apiVersion": "apps/v1", "kind": "DaemonSet", "name": "kps-prometheus-node-exporter",
        "uid": "00000000-0000-0000-0000-000000000001", "controller": True}])
    obj["spec"].update(hostPID=True, serviceAccountName="kps-prometheus-node-exporter")
    negatives.append(("forged exporter", obj))
    return positives, negatives


def subset(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and subset(v, actual[k])
                                               for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(
            subset(a, b) for a, b in zip(expected, actual))
    return expected == actual


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--rendered", type=Path, required=True)
    parser.add_argument("--admin-user", required=True, help="platform-admin OIDC subject")
    parser.add_argument("--admin-group", required=True, help="configured OIDC platform-admin group")
    parser.add_argument("--argo-user", help="default: rendered Argo application-controller SA")
    args = parser.parse_args(argv)
    documents = [doc for path in sorted(args.rendered.rglob("*.yaml"))
                 for doc in yaml.safe_load_all(path.read_text()) if isinstance(doc, dict)]
    policies = [d for d in documents if d.get("kind") in (
        "ValidatingAdmissionPolicy", "ValidatingAdmissionPolicyBinding")]
    monitoring = [d["metadata"]["name"] for d in documents if d.get("kind") == "Namespace"
                  and d["metadata"].get("labels", {}).get("pod-security.kubernetes.io/enforce") == "privileged"]
    argo = [d["metadata"]["namespace"] for d in documents if d.get("kind") == "NetworkPolicy"
            and d["metadata"]["name"] == "allow-repo-server-git-egress"]
    if len(monitoring) != 1 or len(argo) != 1 or len(policies) != 6:
        parser.error("--rendered must point to the hardening output with namespaces and six admission objects")
    base = ["kubectl", "--kubeconfig", args.kubeconfig]
    # Read installed policy specs: absent/stale controls must never yield false confidence.
    for policy in policies:
        result = subprocess.run(base + ["get", policy["kind"], policy["metadata"]["name"], "-o", "json"],
                                capture_output=True, text=True, check=False)
        if result.returncode or not subset(policy["spec"], json.loads(result.stdout)["spec"]):
            print("FAIL: rendered policy is absent or differs: " + policy["metadata"]["name"], file=sys.stderr)
            return 1
    identities = [(args.argo_user or f"system:serviceaccount:{argo[0]}:argocd-application-controller",
                   ["--as-group", "system:serviceaccounts", "--as-group",
                    f"system:serviceaccounts:{argo[0]}", "--as-group", "system:authenticated"]),
                  (args.admin_user, ["--as-group", args.admin_group, "--as-group", "system:authenticated"])]
    positives, negatives = cases(monitoring[0])
    # All positives across both identities precede every negative.
    for admitted, batch in ((True, positives), (False, negatives)):
        for identity, groups in identities:
            for label, obj in batch:
                result = subprocess.run(base + ["--as", identity] + groups + [
                    "create", "--dry-run=server", "-f", "-", "-o", "json"],
                    input=json.dumps(obj), capture_output=True, text=True, check=False)
                expected_denial = any(p["metadata"]["name"] in result.stderr for p in policies)
                passed = result.returncode == 0 if admitted else result.returncode != 0 and expected_denial
                print(("PASS" if passed else "FAIL") + f": {identity}: {label}")
                if not passed:
                    print(result.stderr, file=sys.stderr)
                    return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

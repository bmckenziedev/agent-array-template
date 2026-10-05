#!/usr/bin/env python3
"""Prove the ARC runner admission policy (policy.yaml) against the live API server.

Default mode creates NOTHING: every case is `kubectl create --dry-run=server`, which runs the
full admission chain (mutating plugins incl. RuntimeClass/ServiceAccount, the
ValidatingAdmissionPolicy, ResourceQuota) and then discards the object.

    python3 ci/arc/admission/check_admission.py [--kubeconfig PATH] [--live]

ALLOW cases are the runner pods exactly as the ARC controller builds them: from the live
AutoscalingRunnerSet templates (what runs now) AND from the repo's helm values (what the next
`helm upgrade` would render), each with the controller's additions (name <scale set>-runner-<5>,
the no-permission ServiceAccount, the JIT-config secretKeyRef, the EphemeralRunner owner), plus
the README's netcheck probe pod in both namespaces. DENY cases change one thing each, and a
denial only counts when this policy refused it with the expected rule's message (not schema
validation, RBAC or another policy).

--live additionally creates, and always deletes again, a few temporary objects to exercise the
paths a dry run cannot: a conforming pod in agent-array-arc-runners (target for pod UPDATEs and for
ephemeral containers, i.e. `kubectl debug`, via the pods/ephemeralcontainers subresource with
?dryRun=All), a Job and a ReplicaSet with a hostPath template (their controllers' pod creations
must be refused), and a ServiceAccount named like the chart's (a pod may not use it). Needs an
admin kubeconfig (impersonation, raw subresource PUT).

Exit 0 = every case behaved as expected.
"""
from __future__ import annotations

import argparse
import copy
import json
import random
import subprocess
import sys
import time
from pathlib import Path

import yaml  # PyYAML: only to read the helm values files

HERE = Path(__file__).resolve().parent
ARC = HERE.parents[1]
import os
RENDERED = Path(os.environ.get('ARC_RENDERED_DIR', str(ARC)))
POLICY = "arc-runner-pods"
DENIED_BY = f"ValidatingAdmissionPolicy '{POLICY}'"
POOLS = {  # scale set -> (namespace, runtime class, repo values file)
    "arc-light": ("agent-array-arc-runners", "gvisor", RENDERED / "helm/light/values.yaml"),
    "arc-heavy": ("agent-array-arc-heavy", "kata", RENDERED / "helm/heavy/values.yaml"),
}
APP_KEY_SECRET = "arc-github-app"  # the GitHub App private key, present in both namespaces
CONTROLLER = "system:serviceaccount:agent-array-arc-systems:arc-gha-rs-controller"
LOCAL_PATH = "system:serviceaccount:kube-system:local-path-provisioner-service-account"
# k8s generateName alphabet; random so a dry run never collides with a real runner pod
SUFFIX = "".join(random.choice("bcdfghjklmnpqrstvwxz2456789") for _ in range(5))
LIVE_POD = "arc-admission-check"
SCALE_SET_NAMES = {}
RUNTIME_NAMES = {}


def kubectl(kube, args, data=None):
    p = subprocess.run([*kube, *args], input=data, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def last_line(err):
    return (err.strip().splitlines() or ["?"])[-1]


def runner_pod(template: dict, scale_set: str, name: str | None = None) -> dict:
    """A Pod the way the EphemeralRunner controller (ARC 0.15) creates it from the template."""
    ns = POOLS[scale_set][0]
    actual_scale_set = SCALE_SET_NAMES.get(scale_set, scale_set)
    name = name or f"{actual_scale_set}-runner-{SUFFIX}"
    spec = copy.deepcopy(template["spec"])
    spec.setdefault("restartPolicy", "Never")
    spec.setdefault("serviceAccountName", "arc-light-runner" if ns == "agent-array-arc-runners" else "arc-heavy-runner")
    runner = next(c for c in spec["containers"] if c["name"] == "runner")
    runner.setdefault("env", []).append({"name": "ACTIONS_RUNNER_INPUT_JITCONFIG", "valueFrom": {
        "secretKeyRef": {"name": name, "key": "jitToken"}}})
    meta = copy.deepcopy(template.get("metadata") or {})
    meta.update(name=name, namespace=ns)
    meta.setdefault("labels", {}).update({
        "actions-ephemeral-runner": "True", "app.kubernetes.io/component": "runner",
        "actions.github.com/scale-set-name": scale_set, "actions.github.com/scale-set-namespace": ns})
    meta.setdefault("annotations", {}).update({"actions.github.com/runner-scale-set-name": scale_set})
    meta["ownerReferences"] = [{"apiVersion": "actions.github.com/v1alpha1", "kind": "EphemeralRunner",
                                "name": name, "controller": True,
                                "uid": "00000000-0000-0000-0000-000000000000"}]
    return {"apiVersion": "v1", "kind": "Pod", "metadata": meta, "spec": spec}


def netcheck_pod(ns: str, runtime: str) -> dict:
    runtime = RUNTIME_NAMES.get(runtime, runtime)
    return {'apiVersion': 'v1', 'kind': 'Pod',
            'metadata': {'name': 'arc-netcheck', 'namespace': ns},
            'spec': {'runtimeClassName': runtime, 'automountServiceAccountToken': False,
                     'containers': [{'name': 'probe', 'image': 'busybox:1.36'}]}}


def mut(base: dict, fn) -> dict:
    o = copy.deepcopy(base)
    fn(o, o["spec"])
    return o


def ctr(spec, name):
    for c in spec.get("containers", []) + spec.get("initContainers", []):
        if c["name"] == name:
            return c
    raise KeyError(name)


def sc(c):
    return c.setdefault("securityContext", {})


def build_cases(light: dict, heavy: dict, light_repo: dict, heavy_repo: dict):
    """(name, allow, object, expected message fragment for a deny, --as user)."""
    def add_vol(v, mount_in="runner"):
        def f(o, s):
            s.setdefault("volumes", []).append(v)
            ctr(s, mount_in).setdefault("volumeMounts", []).append({"name": v["name"], "mountPath": "/x"})
        return f

    def runner_env(e):
        return lambda o, s: ctr(s, "runner").setdefault("env", []).append(e)

    def rename(new):
        def f(o, s):
            o["metadata"]["name"] = new
            o["metadata"]["ownerReferences"][0]["name"] = new
            for e in ctr(s, "runner")["env"]:
                if e["name"] == "ACTIONS_RUNNER_INPUT_JITCONFIG":
                    e["valueFrom"]["secretKeyRef"]["name"] = new
        return f

    def node_debug(o, s):  # what `kubectl debug node/<node> -n <ns>` creates, keeping the sandbox
        s.update(hostPID=True, hostNetwork=True, hostIPC=True)
        add_vol({"name": "host-root", "hostPath": {"path": "/"}})(o, s)

    hostpath = {"name": "host", "hostPath": {"path": "/var/empty-admission-check", "type": "Directory"}}
    claim_tpl = {"spec": {"accessModes": ["ReadWriteOnce"], "storageClassName": "local-path",
                          "resources": {"requests": {"storage": "1Gi"}}}}
    out_of_scope = netcheck_pod("agent-array-arc-systems", "gvisor")
    out_of_scope["spec"].pop("runtimeClassName")
    key_ref = {"name": "X", "valueFrom": {"secretKeyRef": {"name": APP_KEY_SECRET, "key": "github_app_private_key"}}}
    rt, hn, vol, hp, ann = "runtimeClassName", "hostNetwork", "volumes may only", "hostPort", "annotations"
    priv, cap, sa, tok, jit = "privileged is allowed", "added capabilities", "serviceAccountName", \
        "automountServiceAccountToken", "own runner's JIT Secret"
    return [
        ("arc-light runner pod (live AutoscalingRunnerSet template)", True, light, None, CONTROLLER),
        ("agent-array-arc-heavy runner pod (live AutoscalingRunnerSet template)", True, heavy, None, CONTROLLER),
        ("arc-light runner pod (repo values-arc-light.yaml)", True, light_repo, None, CONTROLLER),
        ("agent-array-arc-heavy runner pod (repo values-arc-heavy.yaml)", True, heavy_repo, None, CONTROLLER),
        ("netcheck probe pod in agent-array-arc-runners (gvisor)", True, netcheck_pod("agent-array-arc-runners", "gvisor"), None, None),
        ("netcheck probe pod in agent-array-arc-heavy (kata)", True, netcheck_pod("agent-array-arc-heavy", "kata"), None, None),
        ("arc-light runner pod created by local-path-provisioner", False, light, jit, LOCAL_PATH),
        # binding scope: agent-array-arc-systems (controller, listeners) is not selected, so runc stays allowed there
        ("agent-array-arc-systems pod without runtimeClassName (namespace not bound)", True, out_of_scope, None, None),
        # runtime class
        ("light: no runtimeClassName (runc)", False, mut(light, lambda o, s: s.pop("runtimeClassName")), rt, None),
        ("light: runtimeClassName kata", False, mut(light, lambda o, s: s.update(runtimeClassName="kata")), rt, None),
        ("heavy: no runtimeClassName (runc + privileged dind on the node)", False,
         mut(heavy, lambda o, s: s.pop("runtimeClassName")), rt, None),
        ("heavy: runtimeClassName gvisor", False, mut(heavy, lambda o, s: s.update(runtimeClassName="gvisor")), rt, None),
        ("netcheck pod without runtimeClassName", False,
         mut(netcheck_pod("agent-array-arc-runners", "gvisor"), lambda o, s: s.pop("runtimeClassName")), rt, None),
        # host namespaces, host paths, host ports
        ("light: hostNetwork", False, mut(light, lambda o, s: s.update(hostNetwork=True)), hn, None),
        ("heavy: hostPID", False, mut(heavy, lambda o, s: s.update(hostPID=True)), hn, None),
        ("heavy: hostIPC", False, mut(heavy, lambda o, s: s.update(hostIPC=True)), hn, None),
        ("light: hostPath volume", False, mut(light, add_vol(hostpath)), vol, None),
        ("heavy: hostPath volume on the dind sidecar", False, mut(heavy, add_vol(hostpath, "dind")), vol, None),
        ("light: nfs volume (kubelet mounts it on the node)", False,
         mut(light, add_vol({"name": "n", "nfs": {"server": "192.0.2.1", "path": "/"}})), vol, None),
        ("light: secret volume with the GitHub App key", False,
         mut(light, add_vol({"name": "k", "secret": {"secretName": APP_KEY_SECRET}})), vol, None),
        ("heavy: projected volume with a secret source", False,
         mut(heavy, add_vol({"name": "k", "projected": {"sources": [{"secret": {"name": APP_KEY_SECRET}}]}})), vol, None),
        ("light: hostPath pod created by local-path-provisioner", False, mut(light, add_vol(hostpath)), vol, LOCAL_PATH),
        ("light: kubectl debug node/... shaped pod (host namespaces + hostPath /)", False, mut(light, node_debug),
         None, None),
        ("light: persistentVolumeClaim volume", False,
         mut(light, add_vol({"name": "c", "persistentVolumeClaim": {"claimName": "any"}})), vol, None),
        ("heavy: generic ephemeral volume (PVC from a claim template)", False,
         mut(heavy, add_vol({"name": "c", "ephemeral": {"volumeClaimTemplate": claim_tpl}})), vol, None),
        ("light: hostPort on the runner", False, mut(light, lambda o, s: ctr(s, "runner").update(
            ports=[{"containerPort": 8080, "hostPort": 8080}])), hp, None),
        ("heavy: hostPort on an init container", False, mut(heavy, lambda o, s: ctr(s, "init-dind-externals").update(
            ports=[{"containerPort": 8080, "hostPort": 8080}])), hp, None),
        # sandbox config overrides
        ("heavy: io.katacontainers.* annotation", False, mut(heavy, lambda o, s: o["metadata"]["annotations"].update(
            {"io.katacontainers.config.hypervisor.default_vcpus": "8"})), ann, None),
        ("light: dev.gvisor.* annotation", False, mut(light, lambda o, s: o["metadata"]["annotations"].update(
            {"dev.gvisor.flag.network": "host"})), ann, None),
        # privilege and capabilities ("privileged" needs allowPrivilegeEscalation unset/true, or API
        # validation, not admission, refuses it and the case proves nothing)
        ("light: privileged runner", False, mut(light, lambda o, s: sc(ctr(s, "runner")).update(privileged=True)), priv, None),
        ("light: privileged init container named dind", False, mut(light, lambda o, s: s.setdefault(
            "initContainers", []).append({"name": "dind", "image": "docker:dind", "restartPolicy": "Always",
                                          "securityContext": {"privileged": True}})), priv, None),
        ("heavy: privileged runner", False, mut(heavy, lambda o, s: sc(ctr(s, "runner")).update(privileged=True)), priv, None),
        ("heavy: privileged init container other than dind", False,
         mut(heavy, lambda o, s: sc(ctr(s, "init-docker-disk")).update(privileged=True)), priv, None),
        ("heavy: privileged dind as a regular container", False, mut(heavy, lambda o, s: (
            s["containers"].append(ctr(s, "dind")),
            s.update(initContainers=[c for c in s["initContainers"] if c["name"] != "dind"]),
            s["containers"][-1].pop("restartPolicy", None), s["containers"][-1].pop("startupProbe", None))), priv, None),
        ("light: added capability NET_ADMIN", False, mut(light, lambda o, s: sc(ctr(s, "runner")).update(
            capabilities={"add": ["NET_ADMIN"]})), cap, None),
        ("heavy: added capability SYS_ADMIN on the runner", False, mut(heavy, lambda o, s: sc(ctr(s, "runner")).update(
            capabilities={"add": ["SYS_ADMIN"]})), cap, None),
        # service account and secrets
        ("light: automountServiceAccountToken true", False,
         mut(light, lambda o, s: s.update(automountServiceAccountToken=True)), tok, None),
        ("light: secretKeyRef to the GitHub App key", False, mut(light, runner_env(key_ref)), jit, None),
        ("heavy: envFrom the GitHub App key Secret", False, mut(heavy, lambda o, s: ctr(s, "dind").update(
            envFrom=[{"secretRef": {"name": APP_KEY_SECRET}}])), jit, None),
        ("light: another runner's JIT Secret", False, mut(light, runner_env({"name": "Y", "valueFrom": {
            "secretKeyRef": {"name": "arc-light-runner-zzzzz", "key": "jitToken"}}})), jit, None),
        (f"light: pod named {APP_KEY_SECRET} reading its 'own' Secret", False, mut(light, rename(APP_KEY_SECRET)), jit, None),
    ]


def dry_create(kube, obj, as_user=None):
    extra = ["--as", as_user] if as_user else []
    return kubectl(kube, ["create", "--dry-run=server", *extra, "-o", "name", "-f", "-"], json.dumps(obj))


def judge(name, allow, result, expect):
    rc, _, err = result
    allowed = rc == 0
    if not allowed and allow and "AlreadyExists" in err:
        allowed, err = True, "admitted; then AlreadyExists (a real object has this name)"
    if not allowed and allow and "exceeded quota" in err and DENIED_BY not in err:
        # ResourceQuota is the LAST validating admission step: the policy admitted the pod
        allowed, err = True, "admitted by the policy; then refused by quota (pool busy)"
    ok = allowed == allow
    detail = "" if rc == 0 else last_line(err)
    if ok and not allow and DENIED_BY not in err:
        ok, detail = False, "refused, but not by this policy: " + detail
    elif ok and not allow and expect and expect not in err:
        ok, detail = False, f"refused by another rule than '{expect}': " + detail
    print(f"{'PASS' if ok else 'FAIL'}  {'allow' if allow else 'deny '}  {name}"
          + (f" | {detail[:220]}" if detail else ""))
    return ok


def live_cases(kube) -> tuple[int, int]:
    """Temporary objects; every one is deleted in `finally`. Returns (total, failures)."""
    total = failures = 0
    ns = "agent-array-arc-runners"
    tmp = []  # (kind, name) to delete

    def record(ok):
        nonlocal total, failures
        total += 1
        failures += not ok

    try:
        pod = netcheck_pod(ns, "gvisor")
        pod["metadata"].update(name=LIVE_POD, labels={"app": LIVE_POD})
        pod["spec"]["containers"][0]["command"] = ["sleep", "300"]
        rc, _, err = kubectl(kube, ["create", "-f", "-"], json.dumps(pod))
        if rc != 0:
            print(f"FAIL  allow  live: create conforming pod {ns}/{LIVE_POD} | {last_line(err)}")
            return 1, 1
        tmp.append(("pod", LIVE_POD))
        record(True)
        print(f"PASS  allow  live: create conforming pod {ns}/{LIVE_POD}")
        kubectl(kube, ["-n", ns, "wait", "--for=condition=Ready", f"pod/{LIVE_POD}", "--timeout=120s"])

        # pod UPDATEs (server dry runs)
        record(judge("live: label the running pod", True, kubectl(kube, [
            "-n", ns, "label", "pod", LIVE_POD, "agent-array/admission-check=1", "--dry-run=server"]), None))
        record(judge("live: add an io.katacontainers.* annotation to the running pod", False, kubectl(kube, [
            "-n", ns, "annotate", "pod", LIVE_POD, "io.katacontainers.config.hypervisor.default_vcpus=8",
            "--dry-run=server"]), "annotations"))

        # ephemeral containers (`kubectl debug`): PUT pods/<name>/ephemeralcontainers?dryRun=All
        def debug(ec):
            rc, out, err = kubectl(kube, ["-n", ns, "get", "pod", LIVE_POD, "-o", "json"])
            if rc != 0:
                return rc, out, err
            p = json.loads(out)
            p["spec"].setdefault("ephemeralContainers", []).append(
                {"name": "debugger", "image": pod["spec"]["containers"][0]["image"],
                 "command": ["sleep", "30"], **ec})
            return kubectl(kube, ["replace", "--raw",
                                  f"/api/v1/namespaces/{ns}/pods/{LIVE_POD}/ephemeralcontainers?dryRun=All",
                                  "-f", "-"], json.dumps(p))

        record(judge("live: plain ephemeral (debug) container", True, debug({}), None))
        record(judge("live: privileged ephemeral container (kubectl debug --profile=sysadmin)", False,
                     debug({"securityContext": {"privileged": True}}), "privileged is allowed"))
        record(judge("live: ephemeral container with NET_ADMIN (--profile=netadmin)", False,
                     debug({"securityContext": {"capabilities": {"add": ["NET_ADMIN", "NET_RAW"]}}}),
                     "added capabilities"))
        record(judge("live: ephemeral container reading the GitHub App key", False,
                     debug({"env": [{"name": "K", "valueFrom": {"secretKeyRef": {
                         "name": APP_KEY_SECRET, "key": "github_app_private_key"}}}]}), "own runner's JIT Secret"))

        # another existing ServiceAccount, named like the chart's no-permission one (the ARC
        # controller may create ServiceAccounts and RoleBindings here; only exact names are allowed)
        other_sa = "arc-admission-check-gha-rs-no-permission"
        rc, _, err = kubectl(kube, ["-n", ns, "create", "serviceaccount", other_sa])
        if rc == 0:
            tmp.append(("serviceaccount", other_sa))
        else:
            print(f"      (could not create serviceaccount {other_sa}: {last_line(err)})")
        bad_sa = copy.deepcopy(pod)
        bad_sa["metadata"]["name"] = LIVE_POD + "-sa"
        bad_sa["spec"]["serviceAccountName"] = other_sa
        record(judge(f"live: pod with another existing ServiceAccount ({other_sa})", False,
                     dry_create(kube, bad_sa), "serviceAccountName"))

        # pods created by workload controllers (real objects; their pod creations must be refused)
        tpl = copy.deepcopy(pod)
        tpl["spec"]["containers"][0]["command"] = ["true"]
        tpl["spec"]["volumes"] = [{"name": "host", "hostPath": {"path": "/var/empty-admission-check",
                                                                "type": "Directory"}}]
        tpl["spec"]["containers"][0]["volumeMounts"] = [{"name": "host", "mountPath": "/host"}]
        lbl = {"app": "arc-admission-check-ctl"}
        tpl_meta = {"labels": lbl}
        job = {"apiVersion": "batch/v1", "kind": "Job",
               "metadata": {"name": "arc-admission-check-job", "namespace": ns},
               "spec": {"backoffLimit": 0, "activeDeadlineSeconds": 120, "template": {
                   "metadata": tpl_meta, "spec": tpl["spec"]}}}
        rs_spec = copy.deepcopy(tpl["spec"])
        rs_spec["restartPolicy"] = "Always"
        rs = {"apiVersion": "apps/v1", "kind": "ReplicaSet",
              "metadata": {"name": "arc-admission-check-rs", "namespace": ns},
              "spec": {"replicas": 1, "selector": {"matchLabels": lbl},
                       "template": {"metadata": tpl_meta, "spec": rs_spec}}}
        for kind, obj in (("job", job), ("replicaset", rs)):
            rc, _, err = kubectl(kube, ["create", "-f", "-"], json.dumps(obj))
            if rc != 0:
                record(False)
                print(f"FAIL  deny   live: {kind} with a hostPath pod template | could not create: {last_line(err)}")
                continue
            tmp.append((kind, obj["metadata"]["name"]))
            msg = ""
            for _ in range(30):  # up to ~60 s for the controller's first FailedCreate event
                rc, out, _ = kubectl(kube, ["-n", ns, "get", "events", "-o", "json", "--field-selector",
                                            f"involvedObject.name={obj['metadata']['name']},reason=FailedCreate"])
                items = json.loads(out)["items"] if rc == 0 else []
                msg = next((e["message"] for e in items if DENIED_BY in e.get("message", "")), "")
                if msg:
                    break
                time.sleep(2)
            rc, out, _ = kubectl(kube, ["-n", ns, "get", "pods", "-l", "app=arc-admission-check-ctl", "-o", "name"])
            leaked = out.strip()
            ok = bool(msg) and not leaked
            record(ok)
            print(f"{'PASS' if ok else 'FAIL'}  deny   live: {kind} with a hostPath pod template"
                  + (f" | controller refused: {msg[msg.find(DENIED_BY):][:160]}" if msg else " | no policy refusal seen")
                  + (f" | PODS CREATED: {leaked}" if leaked else ""))
    finally:
        for kind, name in reversed(tmp):
            kubectl(kube, ["-n", ns, "delete", kind, name, "--ignore-not-found", "--wait=true", "--timeout=60s"])
        left = [f"{k}/{n}" for k, n in tmp
                if kubectl(kube, ["-n", ns, "get", k, n, "-o", "name"])[0] == 0]
        print(f"live cleanup: {'deleted ' + ', '.join(f'{k}/{n}' for k, n in tmp) if not left else 'LEFT BEHIND: ' + ', '.join(left)}")
        if left:
            failures += 1
    return total, failures


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--kubeconfig", required=True)
    ap.add_argument("--kubectl", default="kubectl")
    ap.add_argument("--live", action="store_true",
                    help="also run the cases that need temporary real objects (deleted afterwards)")
    a = ap.parse_args()
    kube = [a.kubectl] + (["--kubeconfig", a.kubeconfig] if a.kubeconfig else [])

    rc, out, err = kubectl(kube, ["get", "validatingadmissionpolicybinding", POLICY, "-o", "json"])
    if rc != 0 or kubectl(kube, ["get", "validatingadmissionpolicy", POLICY])[0] != 0:
        sys.exit(f"policy/binding {POLICY} not installed; apply the rendered admission manifest ({last_line(err)})")
    if json.loads(out)["spec"].get("validationActions") != ["Deny"]:
        sys.exit(f"binding {POLICY} is not validationActions [Deny]: deny cases would pass silently")

    live_tpl, repo_tpl = {}, {}
    for scale_set, (ns, _, values) in POOLS.items():
        rendered_values = yaml.safe_load(values.read_text(encoding="utf-8"))
        actual = rendered_values['runnerScaleSetName']
        SCALE_SET_NAMES[scale_set] = actual
        RUNTIME_NAMES['gvisor' if scale_set == 'arc-light' else 'kata'] = rendered_values['template']['spec']['runtimeClassName']
        rc, out, err = kubectl(kube, ["-n", ns, "get", "autoscalingrunnerset", actual, "-o", "json"])
        if rc != 0:
            sys.exit(f"cannot read AutoscalingRunnerSet {ns}/{scale_set}: {last_line(err)}")
        live_tpl[scale_set] = json.loads(out)["spec"]["template"]
        repo_tpl[scale_set] = rendered_values["template"]

    cases = build_cases(runner_pod(live_tpl["arc-light"], "arc-light"), runner_pod(live_tpl["agent-array-arc-heavy"], "agent-array-arc-heavy"),
                        runner_pod(repo_tpl["arc-light"], "arc-light"), runner_pod(repo_tpl["agent-array-arc-heavy"], "agent-array-arc-heavy"))
    from generate_cases import cases as preparation_cases
    for case in preparation_cases():
        if case["name"] in ("ordinary plain pod", "ordinary Deployment", "light optional fields absent") or case["name"].endswith("override"):
            cases.append((case["name"], case["allow"], case["object"], case["message"], case["as"]))
    total, failures = len(cases), 0
    for name, allow, obj, expect, as_user in cases:
        failures += not judge(name + (f" (as {as_user.rsplit(':', 1)[-1]})" if as_user else ""), allow,
                              dry_create(kube, obj, as_user or (CONTROLLER if obj["metadata"].get("namespace") in ("agent-array-arc-runners", "agent-array-arc-heavy") else None)), expect)

    # The binding selects by kubernetes.io/metadata.name; the API server pins that label to the
    # namespace's name (overwriting or removing it is silently undone), so relabelling cannot unbind
    # a namespace. Server dry runs, nothing changes. Uses `kubectl patch`, which prints the server's
    # response: `kubectl label -o ...` prints its own local copy, which shows the label as changed.
    label = "kubernetes.io/metadata.name"
    for ns in ("agent-array-arc-runners", "agent-array-arc-heavy"):
        for how, ptype, patch in (
                ("overwrite", "merge", {"metadata": {"labels": {label: "unbound"}}}),
                ("remove", "json", [{"op": "remove", "path": "/metadata/labels/" + label.replace("/", "~1")}])):
            total += 1
            rc, out, err = kubectl(kube, ["patch", "ns", ns, "--type", ptype, "-p", json.dumps(patch),
                                          "--dry-run=server", "-o", "json"])
            got = (json.loads(out)["metadata"].get("labels") or {}).get(label) if rc == 0 else None
            ok = got == ns or (rc != 0 and label in err)
            failures += not ok
            print(f"{'PASS' if ok else 'FAIL'}  deny   {how} label {label} on ns {ns} (would unbind it)"
                  f" | {'server keeps ' + label + '=' + str(got) if rc == 0 else last_line(err)[:200]}")

    if a.live:
        t, f = live_cases(kube)
        total, failures = total + t, failures + f
    else:
        print("SKIP  live cases (pod UPDATE, ephemeral containers, Job/ReplicaSet pods, other SA): pass --live")
    print(f"\n{total - failures}/{total} cases as expected")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

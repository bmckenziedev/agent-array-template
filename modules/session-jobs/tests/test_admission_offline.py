"""Evaluate the checked-in CEL with cel-python; no Kubernetes calls.

This catches regressions locally; Kubernetes type-checking/defaulting still needs
the documented server dry runs before any binding rollout.
"""
import copy
import unittest
from pathlib import Path

import celpy
import yaml
from celpy import json_to_cel

ROOT = Path(__file__).resolve().parents[1]
TASK = "0123456789abcdef"
UID = "12345678-1234-1234-1234-123456789abc"
CONTROLLER = "system:serviceaccount:kube-system:job-controller"
import importlib.util
import json
import re

FIXTURE = json.loads((ROOT / "tests/fixtures/org.fixture.json").read_text())
FIXTURE["org"].setdefault("modules", {}).setdefault("session-jobs", {})["enabled"] = True
spec = importlib.util.spec_from_file_location("job_render", ROOT / "render_plugin.py")
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)
RUNTIME, KEYS = plugin.resolved_keys(FIXTURE)
EMITTED = {}
plugin.render(FIXTURE, lambda path, text: EMITTED.update({path: text}))
MANIFESTS = list(yaml.safe_load_all(EMITTED["global/modules/session-jobs/k8s/admission-policy.yaml"]))
POLICIES = {d["metadata"]["name"]: d for d in MANIFESTS if d["kind"] == "ValidatingAdmissionPolicy"}
BINDINGS = {d["metadata"]["name"]: d for d in MANIFESTS if d["kind"] == "ValidatingAdmissionPolicyBinding"}



def job():
    raw = plugin.subst((ROOT / "task-job.template.yaml").read_text(), KEYS)
    for key, value in {"TASK_ID": TASK, "USER": "ana", "TEAM": "platform",
                       "ACCOUNT": "local", "TASK_TOKEN": "unit-task", "LITELLM_KEY": "unit-key",
                       "TASK_MODELS": "local-coder"}.items():
        raw = raw.replace("__" + key + "__", value)
    return yaml.safe_load(raw)



def pod():
    j = job()
    return {"apiVersion": "v1", "kind": "Pod", "metadata": {
        "namespace": KEYS["NS_SESSION_JOBS"], "name": "task-" + TASK + "-abcde",
        "generateName": "task-" + TASK + "-",
        "labels": {**j["spec"]["template"]["metadata"]["labels"],
            "batch.kubernetes.io/job-name": "task-" + TASK,
            "batch.kubernetes.io/controller-uid": UID},
        "ownerReferences": [{"apiVersion": "batch/v1", "kind": "Job",
            "name": "task-" + TASK, "uid": UID, "controller": True,
            "blockOwnerDeletion": True}]}, "spec": j["spec"]["template"]["spec"]}


def evaluate(obj, identity=CONTROLLER):
    # Exercise the actual namespace binding scope, including ordinary workloads.
    kind = obj["kind"].lower()
    if kind not in ("pod", "job"):
        return True, []
    name = f"{KEYS['PROJECT_NAME']}-session-jobs-{kind}-sandbox"
    selector = BINDINGS[name]["spec"]["matchResources"]["namespaceSelector"]["matchLabels"]
    if selector != {"kubernetes.io/metadata.name": obj["metadata"].get("namespace", "default")}:
        return True, []
    policy = POLICIES[name]["spec"]
    env = celpy.Environment()
    values = {"object": json_to_cel(obj), "request": json_to_cel({
        "operation": "CREATE", "userInfo": {"username": identity}})}
    variables = {}
    for v in policy["variables"]:
        values["variables"] = celpy.celtypes.MapType({celpy.celtypes.StringType(k): v for k, v in variables.items()})
        variables[v["name"]] = env.program(env.compile(v["expression"])).evaluate(values)
    values["variables"] = celpy.celtypes.MapType({celpy.celtypes.StringType(k): v for k, v in variables.items()})
    denied, errors = [], []
    for v in policy["validations"]:
        try:
            if not env.program(env.compile(v["expression"])).evaluate(values):
                denied.append(v["message"])
        except celpy.CELEvalError as exc:
            errors.append(str(exc))
    return not (denied or errors), errors


class AdmissionTests(unittest.TestCase):
    def test_template_job_and_realistic_controller_pod(self):
        for obj in (job(), pod()):
            self.assertEqual(evaluate(obj), (True, []))

    def test_plain_optional_field_regression(self):
        plain = {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "ordinary",
            "namespace": "default"}, "spec": {"containers": [{"name": "busybox",
                "image": "busybox:1.36"}]}}
        self.assertEqual(evaluate(plain), (True, []))
        plain["metadata"]["namespace"] = KEYS["NS_SESSION_JOBS"]
        self.assertEqual(evaluate(plain), (False, []))
        deployment = {"kind": "Deployment", "metadata": {"namespace": "default"},
            "spec": {"template": plain}}
        self.assertEqual(evaluate(deployment), (True, []))

    def test_wrong_identity_and_forged_owner_shape(self):
        for identity in ("system:kube-controller-manager", "human", "system:serviceaccount:agent-array:panel"):
            self.assertEqual(evaluate(pod(), identity), (False, []))
        for field, value in (("uid", ""), ("name", "task-" + "f" * 16),
                             ("kind", "StatefulSet"), ("controller", False)):
            obj = pod()
            obj["metadata"]["ownerReferences"][0][field] = value
            self.assertEqual(evaluate(obj), (False, []))
        for change in (lambda m: m.pop("ownerReferences"), lambda m: m.pop("generateName"),
                       lambda m: m["labels"].update({"batch.kubernetes.io/controller-uid": "forged"}),
                       lambda m: m["labels"].update({"batch.kubernetes.io/job-name": "forged"})):
            obj = pod()
            change(obj["metadata"])
            self.assertEqual(evaluate(obj), (False, []))

    def test_gvisor_rendered_job_passes_actual_policy(self):
        from unittest.mock import patch
        model = copy.deepcopy(FIXTURE)
        model["org"]["modules"]["session-jobs"]["runtime"] = "gvisor"
        model["org"]["modules"].setdefault("seccomp-gvisor", {})["enabled"] = True
        emitted = {}
        plugin.render(model, lambda path, text: emitted.update({path: text}))
        manifests = list(yaml.safe_load_all(emitted["global/modules/session-jobs/k8s/admission-policy.yaml"]))
        policies = {d["metadata"]["name"]: d for d in manifests if d["kind"] == "ValidatingAdmissionPolicy"}
        obj = job()
        podspec = obj["spec"]["template"]["spec"]
        podspec["runtimeClassName"] = model["keys"]["RUNTIME_CLASS_GVISOR"]
        podspec["nodeSelector"] = {model["keys"]["LABEL_PREFIX"] + "/runtime-" + podspec["runtimeClassName"]: "true"}
        podspec["securityContext"]["seccompProfile"] = {
            "type": "Localhost", "localhostProfile": "profiles/agent-array/runtime-default-clone3-enosys.json"}
        with patch.dict(POLICIES, policies):
            self.assertEqual(evaluate(obj), (True, []))
            podspec["securityContext"]["seccompProfile"]["localhostProfile"] = "other.json"
            self.assertEqual(evaluate(obj), (False, []))

    def test_security_negative_matrix(self):
        mutations = [
            lambda s: s.pop("initContainers"),
            lambda s: s.pop("volumes"),
            lambda s: s["containers"][0]["env"].append({"name": "WORKSPACE", "value": "/testbox"}),
            lambda s: s["containers"][0]["env"].append({"name": "BASH_ENV", "value": "/workspace/work/hook.sh"}),
            lambda s: next(e for e in s["containers"][0]["env"] if e["name"] == "WORKSPACE").update(value="/testbox"),
            lambda s: s.update(initContainers=[]),
            lambda s: s["containers"][0].pop("env"),
            lambda s: s["containers"][0].update(command=["sh"]),
            lambda s: s["containers"][0].update(image="ghcr.io/example-org/agent-array-session-runner:mutable"),
            lambda s: s["initContainers"][0]["env"].append({"name": "STOLEN", "valueFrom": {
                "secretKeyRef": {"name": "task-" + TASK, "key": "task-token"}}}),
            lambda s: s["initContainers"][0]["env"].append({"name": "LITELLM_KEY", "value": "unit-only"}),
            lambda s: s["containers"].append(copy.deepcopy(s["containers"][0])),
            lambda s: s["volumes"].append({"name": "secret", "secret": {"secretName": "task-" + TASK}}),
            lambda s: s["securityContext"].pop("seccompProfile"),
            lambda s: s["securityContext"].update(seccompProfile={"type": "Unconfined"}),
            lambda s: s.pop("runtimeClassName"),
            lambda s: s["containers"][0]["securityContext"].update(seccompProfile={"type": "Unconfined"}),
            lambda s: s["initContainers"][0]["volumeMounts"][0].update(readOnly=False),
            lambda s: s["initContainers"][0]["securityContext"].update(runAsUser=1000),
            lambda s: s["containers"][0]["securityContext"].update(readOnlyRootFilesystem=False),
            lambda s: s.update(shareProcessNamespace=True),
            lambda s: s.update(dnsPolicy="None"),
            lambda s: s.update(dnsConfig={"nameservers": ["203.0.113.10"]}),
            lambda s: s.update(dnsConfig={"searches": ["example.org"]}),
        ]
        for base in (job(), pod()):
            for n, change in enumerate(mutations):
                with self.subTest(kind=base["kind"], mutation=n):
                    obj = copy.deepcopy(base)
                    spec = obj["spec"] if obj["kind"] == "Pod" else obj["spec"]["template"]["spec"]
                    change(spec)
                    self.assertEqual(evaluate(obj), (False, []))



if __name__ == "__main__":
    unittest.main()

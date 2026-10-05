"""Fixture rendering and security contract tests; no cluster access."""

import json
import re
import unittest
from unittest.mock import patch
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "tests/fixtures/org.fixture.json").read_text())
KEY_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")
OPTIONAL = {
    "initContainers",
    "ephemeralContainers",
    "ports",
    "securityContext",
    "volumes",
    "env",
    "volumeMounts",
    "projected",
    "serviceAccountToken",
    "nodeAffinity",
    "annotations",
    "claimRef",
    "value",
    "args",
    "command",
    "subPath",
    "readOnly",
    "template",
    "persistentVolumeClaim",
    "ownerReferences",
    "nodeSelector",
    "serviceAccountName",
    "runtimeClassName",
    "hostNetwork",
    "hostPID",
    "hostIPC",
    "imagePullSecrets",
    "capabilities",
    "readOnlyRootFilesystem",
    "runAsNonRoot",
    "seccompProfile",
    "lifecycle",
    "livenessProbe",
    "readinessProbe",
    "startupProbe",
    "storageClassName",
    "local",
    "persistentVolumeReclaimPolicy",
}


def subst(text, keys):
    defaults = yaml.safe_load((ROOT.parent / "services/supervisor/org.component.defaults.yaml").read_text())["defaults"]
    keys = {**{"C_SUPERVISOR_" + name.upper(): str(value) for name, value in defaults.items()}, **keys}
    return KEY_RE.sub(lambda m: keys[m[1]], text)


def optional_guard_errors(expression):
    import importlib.util
    spec = importlib.util.spec_from_file_location("shared_cel_lint", ROOT.parent / "tools/ci/lint_cel.py")
    shared = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shared)
    return shared.reads(expression)


def rendered(enabled=True):
    keys = dict(FIXTURE["keys"])
    defaults = yaml.safe_load((ROOT / "org.component.defaults.yaml").read_text())[
        "defaults"
    ]
    keys.update(
        {"C_SESSIONS_" + name.upper(): str(value) for name, value in defaults.items()}
    )
    sup_defaults = yaml.safe_load((ROOT.parent / "services/supervisor/org.component.defaults.yaml").read_text())["defaults"]
    keys.update({"C_SUPERVISOR_" + name.upper(): str(value) for name, value in sup_defaults.items()})
    model = json.loads(json.dumps(FIXTURE))
    model["org"].setdefault("components", {})["supervisor"] = {"enabled": enabled}
    import sys
    sys.path.insert(0, str(ROOT.parent / "tools/render"))
    from aa_render.templates import condition
    for path in sorted(ROOT.rglob("*.tmpl.yaml")):
        if any((parent / "RENDER-IF").exists() and not condition((parent / "RENDER-IF").read_text(), model)
               for parent in path.parents if parent == ROOT or ROOT in parent.parents):
            continue
        match = re.search(r"\.per-([a-z-]+)\.tmpl", path.name)
        entities = FIXTURE["entities"][match[1].replace("-", "_")] if match else [{}]
        for entity in entities:
            if match and match[1] == "user-tool":
                tool_parts = set(path.parts) & {"claude", "codex", "kimi"}
                if tool_parts and entity["TOOL"] not in tool_parts:
                    continue
            merged = dict(keys, **entity)
            for obj in yaml.safe_load_all(subst(path.read_text(), merged)):
                if obj:
                    yield path, entity, obj


class Templates(unittest.TestCase):
    def test_every_template_renders_and_namespaces_are_unique(self):
        objects = list(rendered())
        self.assertTrue(objects, "session templates missing")
        namespaces = [
            o["metadata"]["name"] for _, _, o in objects if o["kind"] == "Namespace"
        ]
        self.assertCountEqual(
            namespaces, [u["USER_NS"] for u in FIXTURE["entities"]["user"]]
        )
        statefulsets = [
            (entity["USER_SLUG"], entity["TOOL"])
            for _, entity, obj in objects
            if obj["kind"] == "StatefulSet"
        ]
        self.assertCountEqual(
            statefulsets,
            [
                (entity["USER_SLUG"], entity["TOOL"])
                for entity in FIXTURE["entities"]["user_tool"]
            ],
        )
        for path, entity, obj in objects:
            with self.subTest(path=path, entity=entity.get("ENTITY_ID")):
                if entity and obj["kind"] != "Namespace":
                    self.assertEqual(
                        obj["metadata"].get("namespace"), entity["USER_NS"]
                    )
                if obj["kind"] == "StatefulSet":
                    self.assertEqual(obj["metadata"]["name"], entity["TOOL_STS_NAME"])
                    self.assertEqual(
                        obj["spec"]["replicas"], int(entity["TOOL_REPLICAS"])
                    )
                    if entity["USER_SLUG"] == "cy":
                        self.assertEqual(obj["spec"]["replicas"], 0)
                    spec = obj["spec"]["template"]["spec"]
                    self.assertEqual(
                        spec["nodeSelector"]["kubernetes.io/hostname"],
                        entity["TOOL_HOME_NODE"],
                    )
                    for container in spec.get("containers", []) + spec.get(
                        "initContainers", []
                    ):
                        self.assertRegex(container["image"], r"@sha256:[a-f0-9]{64}$")
                    claims = [
                        v["persistentVolumeClaim"]["claimName"]
                        for v in spec.get("volumes", [])
                        if "persistentVolumeClaim" in v
                    ]
                    self.assertEqual(claims, [entity["TOOL_HOME_CLAIM"]])
                    login_names = {
                        v["name"]
                        for v in spec.get("volumes", [])
                        if "persistentVolumeClaim" in v
                    }
                    for container in spec["containers"]:
                        mounts = [
                            v
                            for v in container.get("volumeMounts", [])
                            if v["name"] in login_names
                        ]
                        if container["name"] == "estate":
                            self.assertEqual(mounts, [])
                        if container["name"] == "usage":
                            self.assertEqual(len(mounts), 1)
                            self.assertEqual(mounts[0]["subPath"], "sessions")
                            self.assertTrue(mounts[0]["readOnly"])
                if obj["kind"] == "PersistentVolumeClaim":
                    self.assertEqual(obj["metadata"]["name"], entity["TOOL_HOME_CLAIM"])
                if entity.get("USER_SLUG") == "bo":
                    self.assertNotEqual(entity.get("TOOL"), "codex")

    def test_bindings_are_namespace_selected(self):
        bindings = [
            o
            for _, _, o in rendered()
            if o["kind"] == "ValidatingAdmissionPolicyBinding"
        ]
        self.assertTrue(bindings)
        prefix = FIXTURE["keys"]["LABEL_PREFIX"]
        for binding in bindings:
            selection = binding["spec"]["matchResources"]
            if "namespaceSelector" in selection:
                self.assertEqual(
                    selection["namespaceSelector"]["matchLabels"][prefix + "/kind"],
                    "user-sessions",
                )
            elif "objectSelector" in selection:
                self.assertEqual(binding["spec"]["policyName"], "aa-session-login-pv")
                self.assertEqual(
                    selection["objectSelector"]["matchLabels"][prefix + "/kind"],
                    "login-storage",
                )
            else:
                self.assertEqual(binding["spec"]["policyName"], "aa-session-login-pv")
                for rule in selection["resourceRules"]:
                    self.assertEqual(rule["resources"], ["persistentvolumes"])
                    self.assertEqual(rule["scope"], "Cluster")

    def test_optional_cel_fields_are_guarded(self):
        for path, _, obj in rendered():
            if obj["kind"] == "ValidatingAdmissionPolicy":
                rules = (
                    obj["spec"].get("validations", [])
                    + obj["spec"].get("variables", [])
                    + obj["spec"].get("matchConditions", [])
                )
            elif obj["kind"] == "ValidatingWebhookConfiguration":
                rules = [
                    rule
                    for webhook in obj["webhooks"]
                    for rule in webhook.get("matchConditions", [])
                ]
            else:
                continue
            for rule in rules:
                expression = rule.get("expression", "")
                self.assertEqual(
                    optional_guard_errors(expression), [], str(path) + " " + expression
                )

    def test_guard_checker_rejects_unguarded_reads(self):
        self.assertEqual(
            optional_guard_errors("object.spec.volumes.all(v, true)"),
            ["object.spec.volumes"],
        )
        self.assertEqual(
            optional_guard_errors(
                "!has(object.spec.volumes) || object.spec.volumes.all(v, true)"
            ),
            [],
        )

    def test_guard_checker_rejects_unrelated_receiver_guards(self):
        self.assertEqual(
            optional_guard_errors(
                "has(other.spec.volumes) && object.spec.volumes.all(v, true)"
            ),
            ["object.spec.volumes"],
        )
        self.assertEqual(
            optional_guard_errors(
                "has(c.env) && c.env.all(e, has(e.value) && e.value == 'x')"
            ),
            [],
        )
        self.assertEqual(
            optional_guard_errors("has(c.env) && c.env.all(e, e.value == 'x')"),
            ["e.value"],
        )

    def test_home_hostname_is_only_entity_placeholder(self):
        for path in ROOT.rglob("*.tmpl.yaml"):
            for line in path.read_text().splitlines():
                if "kubernetes.io/hostname:" in line:
                    self.assertIn("{{TOOL_HOME_NODE}}", line)


class PlainTemplates(Templates):
    """Repeat the existing assertions with the plain RENDER-IF variant."""
    def setUp(self):
        original = rendered
        gate = patch(__name__ + ".rendered", side_effect=lambda: original(False))
        gate.start()
        self.addCleanup(gate.stop)

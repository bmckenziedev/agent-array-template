import copy
import importlib.util
import json
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "tests/fixtures/org.fixture.json").read_text())
KEY_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")


def subst(text, keys):
    def rep(match):
        if match.group(1) not in keys:
            raise KeyError(match.group(1))
        return keys[match.group(1)]
    return KEY_RE.sub(rep, text)


class MirrorTests(unittest.TestCase):
    def setUp(self):
        self.keys = dict(FIXTURE["keys"])
        self.keys.update(M_PKG_MIRROR_STORAGE="30Gi",
                         M_PKG_MIRROR_NPM_UPSTREAM="https://registry.npmjs.org/",
                         M_PKG_MIRROR_PYPI_UPSTREAM="https://pypi.org/simple/")
        self.docs = []
        for path in sorted((ROOT / "k8s").glob("*.tmpl.yaml")):
            self.docs.extend(yaml.safe_load_all(subst(path.read_text(), self.keys)))
        spec = importlib.util.spec_from_file_location("mirror_plugin", ROOT / "render_plugin.py")
        self.plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.plugin)

    def test_manifests_security_and_images(self):
        self.assertEqual(len(self.docs), 10)
        for doc in self.docs:
            if doc["kind"] == "Namespace":
                self.assertEqual(doc["metadata"]["labels"]["pod-security.kubernetes.io/enforce"], "restricted")
                continue
            self.assertEqual(doc["metadata"]["namespace"], "agent-array-pkg-mirror")
            if doc["kind"] == "Deployment":
                pod = doc["spec"]["template"]["spec"]
                self.assertFalse(pod["automountServiceAccountToken"])
                self.assertEqual(pod["runtimeClassName"], self.keys["RUNTIME_CLASS_VM"])
                for container in pod["containers"] + pod.get("initContainers", []):
                    self.assertRegex(container["image"], r"@sha256:[0-9a-f]{64}$")
                    self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
                    self.assertEqual(container["securityContext"]["capabilities"]["drop"], ["ALL"])
            if doc["kind"] == "Service":
                self.assertEqual(doc["spec"]["type"], "ClusterIP")
                self.assertIn("app.kubernetes.io/name", doc["spec"]["selector"])

    def test_read_only_and_custom_upstreams(self):
        configs = {d["metadata"]["name"]: d["data"] for d in self.docs if d["kind"] == "ConfigMap"}
        npm = yaml.safe_load(configs["verdaccio-config"]["config.yaml"])
        self.assertEqual(npm["auth"]["htpasswd"]["max_users"], -1)
        self.assertNotEqual(npm["packages"]["**"]["publish"], "$all")
        devpi = configs["devpi-config"]
        self.assertIn("--restrict-modify root", devpi["start.sh"])
        self.assertIn("--require-hashes", devpi["install.sh"])
        self.assertIn("--only-binary=:all:", devpi["install.sh"])
        self.assertIn("--mirror-url", devpi["start.sh"])
        custom = dict(self.keys, M_PKG_MIRROR_NPM_UPSTREAM="https://packages.example.org/npm/",
                      M_PKG_MIRROR_PYPI_UPSTREAM="https://packages.example.org/pypi/simple/")
        for path in (ROOT / "k8s").glob("*.tmpl.yaml"):
            text = subst(path.read_text(), custom)
            list(yaml.safe_load_all(text))
            if "verdaccio" in path.name:
                self.assertIn("https://packages.example.org/npm/", text)
            if "devpi" in path.name:
                self.assertIn("https://packages.example.org/pypi/simple/", text)

    def test_module_gate_and_policy(self):
        output = {}
        self.plugin.render(FIXTURE, output.__setitem__)
        self.assertEqual(output, {})
        model = copy.deepcopy(FIXTURE)
        model["org"]["modules"]["pkg-mirror"]["enabled"] = True
        self.plugin.render(model, output.__setitem__)
        self.assertEqual(len(output), 3)
        policies = [json.loads(t) for t in output.values()]
        ingress = [p for p in policies if "Ingress" in p["spec"]["policyTypes"]]
        for policy in ingress:
            peer = policy["spec"]["ingress"][0]["from"][0]
            self.assertEqual(peer["podSelector"]["matchLabels"]["app.kubernetes.io/name"], "session-job")
        egress = next(p for p in policies if "Egress" in p["spec"]["policyTypes"])
        block = egress["spec"]["egress"][1]["to"][0]["ipBlock"]
        self.assertIn("203.0.113.10/32", block["except"])
        self.assertIn("169.254.0.0/16", block["except"])
        self.assertEqual(egress["spec"]["egress"][1]["ports"][0]["port"], 443)
        model["org"]["modules"]["pkg-mirror"]["npm_upstream"] = "https://user:secret@packages.example.org/"
        with self.assertRaises(ValueError):
            self.plugin.render(model, output.__setitem__)


if __name__ == "__main__":
    unittest.main()

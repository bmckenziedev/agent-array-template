import base64
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("seal_secret", HERE / "seal_secret.py")
seal_secret = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seal_secret)


class SealTests(unittest.TestCase):
    def test_fixture_values_parse(self):
        import yaml
        fixture = json.loads((HERE / "tests/fixtures/org.fixture.json").read_text())
        text = (HERE / "helm/sealed-secrets/values.tmpl.yaml").read_text()
        import re
        text = re.sub(r"\{\{([A-Z][A-Z0-9_]*)\}\}", lambda m: fixture["keys"][m[1]], text)
        values = yaml.safe_load(text)
        self.assertIn("@sha256:", values["image"]["tag"])
        self.assertEqual("ClusterIP", values["service"]["type"])
        self.assertEqual({fixture["keys"]["LABEL_PREFIX"] + "/role-control-plane": "true"},
                         values["nodeSelector"])

    def test_controller_egress(self):
        import ipaddress
        import yaml
        plugin_spec = importlib.util.spec_from_file_location("sealed_plugin", HERE / "render_plugin.py")
        plugin = importlib.util.module_from_spec(plugin_spec)
        plugin_spec.loader.exec_module(plugin)
        fixture = json.loads((HERE / "tests/fixtures/org.fixture.json").read_text())
        output = {}
        plugin.render(fixture, lambda path, text: output.update({path: text}))
        policy = yaml.safe_load(output["global/platform/sealed-secrets/k8s/controller-egress.yaml"])
        self.assertEqual("kube-system", policy["metadata"]["namespace"])
        self.assertEqual("sealed-secrets", policy["spec"]["podSelector"]["matchLabels"]["app.kubernetes.io/name"])
        rules = policy["spec"]["egress"]
        endpoints = {str(ipaddress.ip_network(ip)) for ip in json.loads(fixture["keys"]["APISERVER_ENDPOINT_IPS_JSON"])}
        self.assertEqual(endpoints, {peer["ipBlock"]["cidr"] for peer in rules[0]["to"]})
        self.assertEqual(443, rules[1]["ports"][0]["port"])
        self.assertEqual({53}, {port["port"] for port in rules[2]["ports"]})
        fixture["keys"]["APISERVER_ENDPOINT_IPS_JSON"] = "[]"
        with self.assertRaises(ValueError):
            plugin.render(fixture, lambda path, text: None)

    def test_hidden_refuses_pipe(self):
        with patch.object(seal_secret.sys.stdin, "isatty", return_value=False):
            with self.assertRaises(ValueError):
                seal_secret.hidden("TOKEN")

    def test_hidden_refuses_getpass_echo_fallback(self):
        import warnings
        def fallback(_):
            warnings.warn("terminal unavailable", seal_secret.getpass.GetPassWarning)
            self.fail("fallback warning must abort before any echoed read")
        with patch.object(seal_secret.sys.stdin, "isatty", return_value=True), \
             patch.object(seal_secret.getpass, "getpass", side_effect=fallback):
            with self.assertRaises(ValueError):
                seal_secret.hidden("TOKEN")

    def test_hidden_rejects_empty_and_multiline(self):
        with patch.object(seal_secret.sys.stdin, "isatty", return_value=True):
            for value in ["", "a\nb", "a\rb"]:
                with patch.object(seal_secret.getpass, "getpass", return_value=value):
                    with self.assertRaises(ValueError):
                        seal_secret.hidden("TOKEN")

    def test_credentials_only_in_stdin(self):
        secret = seal_secret.build_secret("app", "env", {"TOKEN": "synthetic-test-only"})
        self.assertEqual("synthetic-test-only", base64.b64decode(secret["data"]["TOKEN"]).decode())
        with tempfile.TemporaryDirectory(dir=HERE / "tests") as directory:
            output = Path(directory) / "result.yaml"
            result = type("Result", (), {"returncode": 0, "stdout":
                          "kind: SealedSecret\nspec:\n  encryptedData: {}\n"})()
            with patch.object(seal_secret.shutil, "which", return_value="kubeseal"), \
                 patch.object(seal_secret.subprocess, "run", return_value=result) as run:
                seal_secret.seal(secret, "outside.pem", output)
                self.assertTrue(output.is_file())
                args, kwargs = run.call_args
                self.assertNotIn("synthetic-test-only", str(args))
                self.assertEqual(secret, json.loads(kwargs["input"]))
                self.assertIn("strict", args[0])

    def test_failure_preserves_existing_file(self):
        with tempfile.TemporaryDirectory(dir=HERE / "tests") as directory:
            output = Path(directory) / "result.yaml"
            output.write_text("previous\n")
            result = type("Result", (), {"returncode": 1, "stdout": ""})()
            with patch.object(seal_secret.shutil, "which", return_value="kubeseal"), \
                 patch.object(seal_secret.subprocess, "run", return_value=result):
                with self.assertRaises(ValueError):
                    seal_secret.seal({}, "outside.pem", output)
            self.assertEqual("previous\n", output.read_text())

    def test_namespace_resolver(self):
        with tempfile.TemporaryDirectory(dir=HERE / "tests") as directory:
            org = Path(directory) / "org.yaml"
            org.write_text('namespaces:\n  llm: "example-llm"\n  user_prefix: aa-u-\nother: true\n')
            self.assertEqual("example-llm", seal_secret.namespace_ref(org, "llm"))
            for key in ["missing", "user_prefix"]:
                with self.assertRaises(ValueError):
                    seal_secret.namespace_ref(org, key)

    def test_examples_are_placeholders(self):
        import yaml
        for path in (HERE / "examples").glob("*.yaml"):
            data = yaml.safe_load(path.read_text())
            self.assertTrue(all(value == "<sealed by seal.sh>"
                                for value in data["spec"]["encryptedData"].values()))


if __name__ == "__main__":
    unittest.main()

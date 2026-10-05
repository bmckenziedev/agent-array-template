import copy
import json
from pathlib import Path
import re
import unittest
import yaml

HERE = Path(__file__).resolve().parents[1]


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.docs = {path.name: list(yaml.safe_load_all(path.read_text()))
                     for path in HERE.glob("*.example.yaml")}

    def test_all_examples_parse_and_are_not_render_templates(self):
        self.assertEqual(11, len(self.docs))
        fixture = json.loads((HERE / "tests/fixtures/org.fixture.json").read_text())
        for path in HERE.glob("*.example.yaml"):
            text = path.read_text()
            self.assertNotRegex(text, r"\{\{[A-Z][A-Z0-9_]*\}\}")
            # Global substitution deliberately leaves examples untouched.
            rendered = re.sub(r"\{\{([A-Z][A-Z0-9_]*)\}\}",
                              lambda match: fixture["keys"][match[1]], text)
            self.assertEqual(text, rendered)
            self.assertIn("<namespace>", text)

    def test_namespace_security_and_budget(self):
        namespace, quota, limits = self.docs["namespace.example.yaml"]
        labels = namespace["metadata"]["labels"]
        self.assertEqual("restricted", labels["pod-security.kubernetes.io/enforce"])
        self.assertIn("pod-security.kubernetes.io/enforce-version", labels)
        self.assertEqual("ResourceQuota", quota["kind"])
        self.assertEqual("LimitRange", limits["kind"])

    def test_pod_contract(self):
        pods = [self.docs["deployment.example.yaml"][0]["spec"]["template"]["spec"],
                self.docs["statefulset-postgres.example.yaml"][0]["spec"]["template"]["spec"],
                self.docs["cronjob.example.yaml"][0]["spec"]["jobTemplate"]["spec"]["template"]["spec"]]
        for pod in pods:
            self.assertFalse(pod["automountServiceAccountToken"])
            self.assertFalse(pod["enableServiceLinks"])
            self.assertTrue(pod["securityContext"]["runAsNonRoot"])
            self.assertEqual("RuntimeDefault", pod["securityContext"]["seccompProfile"]["type"])
            for container in pod["containers"]:
                self.assertIn("@sha256:<digest>", container["image"])
                context = container["securityContext"]
                self.assertFalse(context["allowPrivilegeEscalation"])
                self.assertTrue(context["readOnlyRootFilesystem"])
                self.assertEqual(["ALL"], context["capabilities"]["drop"])
                for field in ["requests", "limits"]:
                    self.assertEqual({"cpu", "memory", "ephemeral-storage"},
                                     set(container["resources"][field]))

    def test_clusterip_and_selector_consistency(self):
        for objects in self.docs.values():
            for obj in objects:
                if obj["kind"] == "Service":
                    self.assertEqual("ClusterIP", obj["spec"]["type"])
                    self.assertNotIn("externalIPs", obj["spec"])
        db, service = self.docs["statefulset-postgres.example.yaml"]
        self.assertEqual("db", db["metadata"]["name"])
        self.assertEqual(db["spec"]["selector"]["matchLabels"], service["spec"]["selector"])
        app = self.docs["deployment.example.yaml"][0]
        service = self.docs["service.example.yaml"][0]
        self.assertEqual(app["spec"]["selector"]["matchLabels"], service["spec"]["selector"])

    def test_network_policy_default_deny(self):
        policy = self.docs["default-deny.example.yaml"][0]["spec"]
        self.assertEqual({}, policy["podSelector"])
        self.assertEqual([], policy["egress"])
        self.assertEqual([], policy["ingress"])
        egress = self.docs["allow-egress.example.yaml"][0]["spec"]["egress"]
        self.assertEqual(4, len(egress))
        self.assertNotIn("0.0.0.0/0", str(egress))

    def test_manual_argo_and_fake_encryption(self):
        app = self.docs["argocd-app.example.yaml"][0]
        self.assertNotIn("automated", app["spec"]["syncPolicy"])
        self.assertTrue(app["spec"]["source"]["path"].startswith("rendered/global/"))
        self.assertNotIn("finalizers", app["metadata"])
        secret = self.docs["sealedsecret.example.yaml"][0]
        self.assertEqual({"API_KEY": "<sealed by seal.sh>"}, secret["spec"]["encryptedData"])


if __name__ == "__main__":
    unittest.main()

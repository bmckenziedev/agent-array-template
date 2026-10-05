"""Offline template and conditional security checks."""

import copy
import importlib.util
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((Path(__file__).parent / "fixtures/org.fixture.json").read_text())


def subst(text, keys):
    def replace(match):
        if match.group(1) not in keys:
            raise KeyError(match.group(1))
        return keys[match.group(1)]

    return re.sub(r"\{\{([A-Z][A-Z0-9_]*)\}\}", replace, text)


def plugin(path, model):
    spec = importlib.util.spec_from_file_location(
        "render_test_" + path.parent.name, path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = {}
    module.render(model, lambda name, text: output.update({name: text}))
    return [json.loads(text) for text in output.values()]


class Templates(unittest.TestCase):
    def setUp(self):
        self.model = copy.deepcopy(FIXTURE)
        self.model["org"]["components"] = {
            "panel": {
                "max_active_tasks_per_user": 3,
                "max_active_tasks_per_team": 10,
                "task_models": ["local-coder", "local-coder-small"],
                "task_duration_seconds": 3900,
            }
        }
        self.keys = dict(FIXTURE["keys"])

    def test_all_templates_parse_and_pin_images(self):
        for path in ROOT.rglob("*.tmpl.yaml"):
            if "panel" in path.parts or "tests" in path.parts:
                continue
            for doc in yaml.safe_load_all(subst(path.read_text(), self.keys)):
                if not doc:
                    continue
                self.assertNotEqual(doc["kind"], "Namespace", path)
                if "k8s" in path.parts:
                    self.assertIn(
                        doc["metadata"]["namespace"], [self.keys[k] for k in ("NS_PORTAL", "NS_ARGOCD", "NS_MONITORING")], path
                    )
                if doc["kind"] == "Deployment":
                    spec = doc["spec"]["template"]["spec"]
                    self.assertFalse(spec["automountServiceAccountToken"])
                    for container in spec["containers"]:
                        self.assertRegex(container["image"], r"@sha256:[0-9a-f]{64}$")
                        self.assertTrue(
                            container["securityContext"]["readOnlyRootFilesystem"]
                        )
                if shutil.which("kubeconform") and "k8s" in path.parts:
                    result = subprocess.run(
                        ["kubeconform", "-strict", "-ignore-missing-schemas"],
                        input=yaml.safe_dump(doc),
                        text=True,
                        capture_output=True,
                    )
                    self.assertEqual(
                        result.returncode, 0, result.stderr + result.stdout
                    )

    def test_access_gates_and_host_routes(self):
        for name, kind in [
            ("oauth2-proxy", "oidc-proxy"),
            ("cloudflared", "cloudflare-access"),
        ]:
            self.assertEqual(
                (ROOT / "access" / name / "RENDER-IF").read_text().strip(),
                "org.network.access.kind == " + kind,
            )
        for host in ["panel", "grafana", "argocd", "headlamp"]:
            docs = list(
                yaml.safe_load_all(
                    subst(
                        (
                            ROOT / "access/oauth2-proxy/k8s" / (host + ".tmpl.yaml")
                        ).read_text(),
                        self.keys,
                    )
                )
            )
            args = docs[0]["spec"]["template"]["spec"]["containers"][0]["args"]
            self.assertIn(
                "--redirect-url=https://"
                + self.keys["HOST_" + host.upper()]
                + "/oauth2/callback",
                args,
            )
            self.assertIn("--pass-authorization-header=true", args)
            self.assertFalse(any("insecure" in a for a in args))

    def test_conditional_panel_api_token(self):
        for enabled in [False, True]:
            self.model["org"]["modules"]["session-jobs"]["enabled"] = enabled
            self.model["keys"]["M_SESSION_JOBS_ENABLED"] = str(enabled).lower()
            deployment = plugin(ROOT / "render_plugin.py", self.model)[0]
            spec = deployment["spec"]["template"]["spec"]
            self.assertEqual(spec["automountServiceAccountToken"], enabled)
            self.assertEqual(
                any(v["name"] == "template" for v in spec["volumes"]), enabled
            )
            labels = deployment["spec"]["template"]["metadata"]["labels"]
            self.assertEqual(labels[self.keys["LABEL_PREFIX"] + "/llm-client"], "true")

    def test_headlamp_user_identity(self):
        docs = list(
            yaml.safe_load_all(
                subst((ROOT / "headlamp/k8s/headlamp.tmpl.yaml").read_text(), self.keys)
            )
        )
        spec = docs[0]["spec"]["template"]["spec"]
        self.assertFalse(spec["automountServiceAccountToken"])
        self.assertNotIn(
            "-unsafe-use-service-account-token", spec["containers"][0]["args"]
        )
        config = yaml.safe_load(docs[-1]["data"]["kubeconfig"])
        self.assertEqual(config["users"], [])
        rules = plugin(ROOT / "headlamp/render_plugin.py", self.model)[0]["spec"][
            "egress"
        ]
        self.assertEqual(rules[0]["to"][0]["ipBlock"]["cidr"], "100.64.0.10/32")

    def test_unique_object_names_with_variant_gates(self):
        for variant in ["oauth2-proxy", "cloudflared"]:
            objects = []
            for directory in [
                ROOT / "k8s",
                ROOT / "headlamp/k8s",
                ROOT / "access" / variant,
            ]:
                for path in directory.rglob("*.tmpl.yaml"):
                    objects.extend(
                        doc
                        for doc in yaml.safe_load_all(
                            subst(path.read_text(), self.keys)
                        )
                        if doc
                    )
            objects.extend(plugin(ROOT / "render_plugin.py", self.model))
            objects.extend(plugin(ROOT / "headlamp/render_plugin.py", self.model))
            identities = [
                (
                    doc["kind"],
                    doc["metadata"].get("namespace", ""),
                    doc["metadata"]["name"],
                )
                for doc in objects
            ]
            self.assertEqual(len(identities), len(set(identities)), variant)
        self.assertEqual(
            (ROOT / "api-egress/RENDER-IF").read_text().strip(),
            "org.modules.session-jobs.enabled",
        )

    def test_api_egress_module_guard(self):
        self.assertEqual(plugin(ROOT / "api-egress/render_plugin.py", self.model), [])
        self.model["org"]["modules"]["session-jobs"]["enabled"] = True
        documents = plugin(ROOT / "api-egress/render_plugin.py", self.model)
        self.assertEqual(len(documents), 1)
        self.assertEqual(
            documents[0]["spec"]["podSelector"]["matchLabels"][
                "app.kubernetes.io/name"
            ],
            "panel",
        )

    def test_listener_split(self):
        docs = list(
            yaml.safe_load_all(
                subst((ROOT / "k8s/networkpolicies.tmpl.yaml").read_text(), self.keys)
            )
        )
        public, internal = docs[:2]
        self.assertEqual(public["spec"]["ingress"][0]["ports"][0]["port"], 8080)
        self.assertEqual(internal["spec"]["ingress"][0]["ports"][0]["port"], 8081)
        self.assertEqual(
            internal["spec"]["ingress"][0]["from"][0]["namespaceSelector"][
                "matchLabels"
            ]["kubernetes.io/metadata.name"],
            self.keys["NS_SESSION_JOBS"],
        )


if __name__ == "__main__":
    unittest.main()

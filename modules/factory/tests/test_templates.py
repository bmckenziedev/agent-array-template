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

ROOT = Path(__file__).resolve().parents[1]
KEY_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")


class TemplatesTest(unittest.TestCase):
    def test_enabled_fixture(self):
        fixture = json.loads((ROOT / "tests/fixtures/org.fixture.json").read_text())
        model = copy.deepcopy(fixture)
        model["org"]["modules"]["factory"]["enabled"] = True
        model["keys"]["M_FACTORY_ENABLED"] = "true"
        defaults = yaml.safe_load((ROOT / "org.component.defaults.yaml").read_text())["defaults"]
        for name, value in defaults.items():
            model["keys"]["M_FACTORY_" + name.upper()] = str(value)
        monitoring_defaults = yaml.safe_load((ROOT.parents[1] / "monitoring/org.component.defaults.yaml").read_text())["defaults"]
        model["keys"]["C_MONITORING_FACTORY_STARVATION_MINUTES"] = str(monitoring_defaults["factory_starvation_minutes"])
        objects = []
        with tempfile.TemporaryDirectory() as scratch:
            for template in (ROOT / "k8s").glob("*.tmpl.yaml"):
                rendered = KEY_RE.sub(lambda m: str(model["keys"][m[1]]), template.read_text())
                objects.extend(yaml.safe_load_all(rendered))
                destination = Path(scratch) / template.name.replace(".tmpl", "")
                destination.write_text(rendered, encoding="utf-8")
            if shutil.which("kubeconform"):
                subprocess.run(["kubeconform", "-strict", "-ignore-missing-schemas", scratch], check=True)
        spec = importlib.util.spec_from_file_location("factory_lanes", ROOT / "engine/render_plugin.py")
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        plugin.render(model, lambda path, text: objects.append(json.loads(text)))
        deployment = next(o for o in objects if o["kind"] == "Deployment")
        pod = deployment["spec"]["template"]["spec"]
        self.assertEqual(pod["runtimeClassName"], model["keys"]["RUNTIME_CLASS_VM"])
        self.assertIn("@sha256:", pod["containers"][0]["image"])
        self.assertNotEqual(pod["containers"][0]["imagePullPolicy"], "Never")
        self.assertEqual(pod["securityContext"]["seccompProfile"]["type"], "RuntimeDefault")
        self.assertEqual(pod["imagePullSecrets"][0]["name"], model["keys"]["REGISTRY_PULL_SECRET"])
        self.assertTrue(any(v.get("configMap", {}).get("name") == "org-directory" for v in pod["volumes"]))
        service = next(o for o in objects if o["kind"] == "Service")
        self.assertEqual(service["metadata"]["name"], "factory-api")
        self.assertEqual(service["spec"]["selector"], deployment["spec"]["selector"]["matchLabels"])
        env = {item["name"]: item.get("value") for item in pod["containers"][0]["env"]}
        self.assertEqual(env["FACTORY_MAX_BATCHES"], str(defaults["queue_max_batches"]))
        self.assertEqual(env["FACTORY_STARVATION_N"], str(defaults["starvation_dispatches"]))
        self.assertEqual(env["FACTORY_WORKER_POLL_SECONDS"], str(defaults["worker_poll_seconds"]))
        config = next(o for o in objects if o["kind"] == "ConfigMap")
        lanes = json.loads(config["data"]["lanes.json"])["lanes"]
        for lane in lanes.values():
            self.assertEqual(lane["concurrency"], defaults["lane_concurrency"])
            self.assertEqual(lane["endpoint"]["key_file"], "/var/run/factory/model-auth/token")
        self.assertFalse(any(o["kind"] == "Namespace" for o in objects))
        role = next(o for o in objects if o["kind"] == "ClusterRole")
        self.assertIn("tokenreviews", role["rules"][0]["resources"])
        self.assertIn("namespaces", role["rules"][1]["resources"])
        network = next(o for o in objects if o["kind"] == "NetworkPolicy")
        self.assertEqual(network["spec"]["policyTypes"], ["Ingress", "Egress"])

    def test_all_apiserver_endpoints_and_module_gate(self):
        model = json.loads((ROOT / "tests/fixtures/org.fixture.json").read_text())
        spec = importlib.util.spec_from_file_location("factory_render", ROOT / "render_plugin.py")
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        emitted = {}
        model["org"]["modules"]["factory"]["enabled"] = False
        plugin.render(model, lambda path, text: emitted.update({path: text}))
        self.assertEqual(emitted, {})
        model["org"]["modules"]["factory"]["enabled"] = True
        model["keys"]["APISERVER_ENDPOINT_IPS_JSON"] = '["100.64.0.10","100.64.0.11"]'
        plugin.render(model, lambda path, text: emitted.update({path: text}))
        policy = json.loads(emitted["global/modules/factory/k8s/apiserver.yaml"])
        peers = policy["spec"]["egress"][0]["to"]
        self.assertEqual(len(peers), 3)
        self.assertEqual(policy["metadata"]["namespace"], model["keys"]["NS_FACTORY"])
        again = {}
        plugin.render(model, lambda path, text: again.update({path: text}))
        self.assertEqual(emitted, again)

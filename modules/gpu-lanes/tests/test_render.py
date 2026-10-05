import copy
import json
from pathlib import Path
import re
import unittest

import yaml
import render_plugin as plugin

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    model = json.loads((ROOT / "tests/fixtures/org.fixture.json").read_text())
    model["org"]["modules"]["gpu-lanes"] = {"enabled": True, "lanes": [
        {"name": "lane-gpu-a", "node": "gpu-a", "model_group": "local-coder",
         "gguf_url": "https://models.example.org/coder.gguf", "gguf_sha256": "a" * 64,
         "ctx": 8192, "slots": 2, "extra_args": ["--jinja"]}]}
    return model


def rendered(model):
    output = {}
    plugin.render(model, lambda path, text: output.__setitem__(path, text))
    return output


def subst(text, keys):
    def rep(match):
        if match[1] not in keys:
            raise KeyError(f"unknown placeholder {match[1]}")
        return keys[match[1]]
    return re.sub(r"\{\{([A-Z][A-Z0-9_]*)\}\}", rep, text)


class LaneTests(unittest.TestCase):
    def test_disabled(self):
        model = fixture()
        model["org"]["modules"]["gpu-lanes"]["enabled"] = False
        self.assertEqual(rendered(model), {})

    def test_deterministic_cache_and_auth(self):
        model = fixture()
        output = rendered(model)
        self.assertEqual(output, rendered(copy.deepcopy(model)))
        docs = list(yaml.safe_load_all(next(iter(output.values()))))
        deploy = next(d for d in docs if d["kind"] == "Deployment")
        pod = deploy["spec"]["template"]["spec"]
        self.assertNotIn("kubernetes.io/hostname", pod["nodeSelector"])
        self.assertEqual(pod["runtimeClassName"], model["keys"]["RUNTIME_CLASS_GPU"])
        self.assertTrue(any(v.get("secret", {}).get("secretName") == "model-server-auth" for v in pod["volumes"]))
        self.assertIn("checksum mismatch", pod["initContainers"][0]["command"][-1])
        self.assertIn("--api-key-file", pod["containers"][0]["args"])
        pv = next(d for d in docs if d["kind"] == "PersistentVolume")
        self.assertEqual(pv["spec"]["persistentVolumeReclaimPolicy"], "Retain")
        self.assertIn("gpu-a", json.dumps(pv["spec"]["nodeAffinity"]))
        for c in pod["containers"] + pod["initContainers"]:
            self.assertRegex(c["image"], r"@sha256:[0-9a-f]{64}$")

    def test_invalid_lane_is_rejected(self):
        for key, value in [("gguf_sha256", "0" * 64), ("gguf_url", "http://example.org/a"),
                           ("node", "node-a"), ("slots", 0), ("extra_args", ["--api-key", "bad"]),
                           ("extra_args", ["--api-key=bad"]), ("extra_args", "--host")]:
            model = fixture()
            model["org"]["modules"]["gpu-lanes"]["lanes"][0][key] = value
            with self.assertRaises(ValueError, msg=key):
                rendered(model)

    def test_factory_gated_ingress(self):
        model = fixture()
        def sources():
            docs = list(yaml.safe_load_all(next(iter(rendered(model).values()))))
            return next(d for d in docs if d["kind"] == "NetworkPolicy")["spec"]["ingress"][0]["from"]
        self.assertEqual(len(sources()), 1)
        model["org"]["modules"]["factory"]["enabled"] = True
        self.assertEqual(len(sources()), 2)

    def test_plumbing_templates(self):
        model = fixture()
        docs = []
        for path in (ROOT / "k8s").glob("*.tmpl.yaml"):
            docs.extend(yaml.safe_load_all(subst(path.read_text(), model["keys"])))
        for obj in docs:
            if obj["kind"] == "DaemonSet":
                pod = obj["spec"]["template"]["spec"]
                self.assertEqual(pod["nodeSelector"], {model["keys"]["LABEL_PREFIX"] + "/role-gpu": "true"})
                self.assertEqual(pod["tolerations"][0]["key"], model["keys"]["LABEL_PREFIX"] + "/gpu")
                self.assertFalse(pod["automountServiceAccountToken"])
                self.assertIn("@sha256:", pod["containers"][0]["image"])
            if obj["kind"] == "Namespace":
                self.assertEqual(obj["metadata"]["labels"]["pod-security.kubernetes.io/enforce"], "privileged")

    def test_download_integrity_and_atomicity(self):
        # Substitute only the target directory; exercise the actual init program offline.
        import hashlib
        import io
        import os
        import tempfile
        from unittest.mock import patch
        content = b"synthetic model bytes"
        digest = hashlib.sha256(content).hexdigest()
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as directory:
            script = plugin.DOWNLOAD.replace("'/cache/'", repr(directory + "/"))
            with patch.dict(os.environ, {"MODEL_SHA256": digest, "MODEL_URL": "https://example.org/model"}), patch("urllib.request.urlopen", return_value=io.BytesIO(content)):
                exec(script, {})
            self.assertEqual((Path(directory) / (digest + ".gguf")).read_bytes(), content)
            (Path(directory) / (digest + ".gguf")).unlink()
            with patch.dict(os.environ, {"MODEL_SHA256": digest, "MODEL_URL": "https://example.org/model"}), patch("urllib.request.urlopen", return_value=io.BytesIO(b"wrong")):
                with self.assertRaises(SystemExit):
                    exec(script, {})
            self.assertFalse((Path(directory) / (digest + ".gguf")).exists())

    def test_dcgm_validation(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("dcgm_validate", ROOT / "dcgm/validate.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.validate()

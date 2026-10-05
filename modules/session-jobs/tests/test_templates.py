"""Fixture rendering checks, independent of the organization renderer."""
import copy
import unittest
import yaml
from tests.test_admission_offline import FIXTURE, ROOT, plugin


class TemplateTests(unittest.TestCase):
    def test_all_manifest_templates(self):
        _, keys = plugin.resolved_keys(FIXTURE)
        for path in sorted((ROOT / "k8s").glob("*.tmpl.yaml")):
            for obj in yaml.safe_load_all(plugin.subst(path.read_text(), keys)):
                self.assertEqual(obj["metadata"]["namespace"], keys["NS_SESSION_JOBS"])
                self.assertIn("app.kubernetes.io/part-of", obj["metadata"]["labels"])
                if obj["kind"] == "Role":
                    self.assertEqual(obj["rules"][0]["resources"], ["jobs"])
                    self.assertEqual(obj["rules"][0]["apiGroups"], ["batch"])

    def test_disabled_emits_nothing(self):
        model = copy.deepcopy(FIXTURE)
        model["org"]["modules"]["session-jobs"]["enabled"] = False
        emitted = {}
        plugin.render(model, lambda p, t: emitted.update({p: t}))
        self.assertEqual(emitted, {})

    def test_runtime_and_credential_free_tester(self):
        for runtime in ("kata", "gvisor"):
            model = copy.deepcopy(FIXTURE)
            model["org"]["modules"]["session-jobs"]["runtime"] = runtime
            model["org"]["modules"].setdefault("seccomp-gvisor", {})["enabled"] = True
            emitted = {}
            plugin.render(model, lambda p, t: emitted.update({p: t}))
            cm = yaml.safe_load(emitted["global/modules/session-jobs/k8s/job-template.yaml"])
            job = yaml.safe_load(cm["data"]["task-job.template.yaml"])
            pod = job["spec"]["template"]["spec"]
            self.assertEqual(cm["metadata"]["namespace"], model["keys"]["NS_PORTAL"])
            self.assertEqual(pod["runtimeClassName"], model["keys"]["RUNTIME_CLASS_VM" if runtime == "kata" else "RUNTIME_CLASS_GVISOR"])
            self.assertIn("@sha256:", pod["containers"][0]["image"])
            self.assertFalse(pod["automountServiceAccountToken"])
            self.assertFalse(pod["shareProcessNamespace"])
            tester = pod["initContainers"][0]
            self.assertEqual(tester["securityContext"]["runAsUser"], 1001)
            self.assertTrue(tester["volumeMounts"][0]["readOnly"])
            self.assertFalse(any(e["name"] in ("TASK_TOKEN", "LITELLM_KEY") or "valueFrom" in e for e in tester["env"]))
            profile = pod["securityContext"]["seccompProfile"]
            self.assertEqual(profile["type"], "RuntimeDefault" if runtime == "kata" else "Localhost")

    def test_gvisor_dependency_required(self):
        model = copy.deepcopy(FIXTURE)
        model["org"]["modules"]["session-jobs"]["runtime"] = "gvisor"
        model["org"]["modules"].setdefault("seccomp-gvisor", {})["enabled"] = False
        with self.assertRaises(ValueError):
            plugin.resolved_keys(model)

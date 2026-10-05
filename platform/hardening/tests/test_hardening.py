import copy
import json
import importlib.util
from pathlib import Path
import unittest
import yaml
from render_plugin import render, subst, defaults
from cel_guards import check_expression, check_policy

ROOT = Path(__file__).parents[1]
MODEL = json.loads((ROOT / "tests/fixtures/org.fixture.json").read_text())


class Hardening(unittest.TestCase):
    def output(self, model=MODEL):
        out = {}
        render(model, lambda path, text: out.update({path: list(yaml.safe_load_all(text))}))
        return out

    def test_namespace_contract(self):
        out = self.output()
        names = set(MODEL["org"]["namespaces"].values()) - {MODEL["org"]["namespaces"]["user_prefix"]}
        names.update(["kube-system", "kube-public", "kube-node-lease", "default"])
        objects = [obj for docs in out.values() for obj in docs]
        namespaces = [o for o in objects if o["kind"] == "Namespace"]
        self.assertEqual(names, {o["metadata"]["name"] for o in namespaces})
        self.assertEqual(len(names), len(namespaces))
        for obj in namespaces:
            labels = obj["metadata"]["labels"]
            self.assertEqual("latest", labels["pod-security.kubernetes.io/enforce-version"])
            self.assertEqual("baseline" if obj["metadata"]["name"] == "kube-system" else "restricted", labels["pod-security.kubernetes.io/warn"])
            ns = obj["metadata"]["name"]
            deny = [p for p in objects if p["kind"] == "NetworkPolicy" and
                    p["metadata"] == {"name": "default-deny", "namespace": ns}]
            self.assertEqual(1, len(deny))
            self.assertEqual(["Ingress", "Egress"], deny[0]["spec"]["policyTypes"])
        self.assertTrue(all(p.startswith("global/platform/hardening/k8s/") for p in out))

    def test_psa_table(self):
        table = defaults()["psa"]
        self.assertEqual("privileged", table["monitoring"])
        for key in ["llm", "models", "default"]:
            self.assertEqual("baseline", table[key])
        self.assertEqual("privileged", table["kube-system"])
        self.assertEqual(15, len(table))

    def test_endpoint_service_and_clients(self):
        out = self.output()
        policies = [o for docs in out.values() for o in docs if o["metadata"]["name"] == "allow-apiserver-endpoint"]
        self.assertEqual(8, len(policies))
        for policy in policies:
            egress = policy["spec"]["egress"]
            self.assertEqual(int(MODEL["keys"]["APISERVER_PORT"]), egress[0]["ports"][0]["port"])
            self.assertEqual(MODEL["keys"]["APISERVER_SERVICE_IP"] + "/32", egress[1]["to"][0]["ipBlock"]["cidr"])

    def test_org_override_and_ipv6(self):
        model = copy.deepcopy(MODEL)
        model["org"].setdefault("components", {})["hardening"] = {
            "psa_version": "v1.34", "psa": {"llm": "restricted"}, "apiserver_clients": ["llm"]}
        model["keys"]["APISERVER_ENDPOINT_IPS_JSON"] = '["2001:db8::1"]'
        objects = [o for docs in self.output(model).values() for o in docs]
        policy = next(o for o in objects if o["metadata"]["name"] == "allow-apiserver-endpoint")
        self.assertEqual("2001:db8::1/128", policy["spec"]["egress"][0]["to"][0]["ipBlock"]["cidr"])
        obj = next(o for o in objects if o["kind"] == "Namespace" and o["metadata"]["name"] == model["org"]["namespaces"]["llm"])
        self.assertEqual("restricted", obj["metadata"]["labels"]["pod-security.kubernetes.io/enforce"])

    def test_fail_closed_unknown_namespace(self):
        model = copy.deepcopy(MODEL)
        model["org"]["namespaces"]["extra"] = "new-app"
        with self.assertRaises(ValueError):
            self.output(model)

    def test_templates_and_guards(self):
        for path in (ROOT / "k8s").glob("*.yaml"):
            for obj in yaml.safe_load_all(subst(path.read_text(), MODEL["keys"])):
                self.assertFalse(check_policy(obj), path.name)
                if obj["kind"] == "ValidatingAdmissionPolicyBinding":
                    self.assertIn("namespaceSelector", obj["spec"]["matchResources"])
        self.assertRaises(KeyError, subst, "{{UNKNOWN_KEY}}", {})

    def test_optional_seccomp_policies_pass_same_checker(self):
        module = ROOT.parents[1] / "modules/seccomp-gvisor"
        spec = importlib.util.spec_from_file_location("seccomp_patches", module / "patches/make-patches.py")
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        source = (module / "tests/fixtures/session-jobs/admission.yaml").read_text()
        anchor = '      message: "containers must not be privileged"\n'
        patched = source.replace(anchor, anchor + generator.RULES)
        for policy in yaml.safe_load_all(patched):
            self.assertFalse(check_policy(policy), policy["metadata"]["name"])

    def test_guard_checker_rejects_missing_nested_guard(self):
        self.assertTrue(check_expression("c.securityContext.privileged == false"))
        self.assertTrue(check_expression("has(c.securityContext) && c.securityContext.privileged == false"))
        self.assertFalse(check_expression("!has(c.securityContext) || !has(c.securityContext.privileged) || !c.securityContext.privileged"))


if __name__ == "__main__":
    unittest.main()

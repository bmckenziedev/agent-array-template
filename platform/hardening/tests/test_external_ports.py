"""Evaluate the shipped port policies using CEL, including sparse objects."""
import copy
import json
from pathlib import Path
import unittest
import yaml
from celpy import Environment, json_to_cel, celtypes
from render_plugin import subst

ROOT = Path(__file__).parents[1]
KEYS = json.loads((ROOT / "tests/fixtures/org.fixture.json").read_text())["keys"]
POLICIES = [d["spec"] for d in yaml.safe_load_all(subst(
    (ROOT / "k8s/external-port-admission.tmpl.yaml").read_text(), KEYS))
            if d["kind"] == "ValidatingAdmissionPolicy"]


def admitted(spec, obj):
    env = Environment()
    context = json_to_cel({"object": obj})
    context["variables"] = celtypes.MapType()
    def evaluate(expression):
        return env.program(env.compile(expression)).evaluate(context)
    for variable in spec.get("variables", []):
        context["variables"][variable["name"]] = evaluate(variable["expression"])
    return all(evaluate(item["expression"]) for item in spec["validations"])


class ExternalPorts(unittest.TestCase):
    def test_service_shapes(self):
        for value in ({}, {"type": "ClusterIP"}, {"externalIPs": []}):
            self.assertTrue(admitted(POLICIES[0], {"spec": value}))
        for value in ({"type": "NodePort"}, {"type": "LoadBalancer"},
                      {"externalIPs": ["203.0.113.20"]}, {"type": "ExternalName"}):
            self.assertFalse(admitted(POLICIES[0], {"spec": value}))

    def test_plain_pod_and_deployment(self):
        pod = {"kind": "Pod", "spec": {"containers": [{"name": "busybox", "image": "busybox:1.36"}]}}
        self.assertTrue(admitted(POLICIES[1], pod))
        deployment = {"kind": "Deployment", "spec": {"template": {"spec": pod["spec"]}}}
        self.assertTrue(admitted(POLICIES[1], deployment))

    def test_hostport_in_all_container_types(self):
        for field in ("containers", "initContainers", "ephemeralContainers"):
            for ports, expected in (([{"containerPort": 80}], True),
                                    ([{"containerPort": 80, "hostPort": 0}], True),
                                    ([{"containerPort": 80, "hostPort": 9100}], False)):
                spec = {"containers": [{"name": "busybox", "image": "busybox:1.36"}]}
                spec[field] = [{"name": "other", "ports": ports}]
                for kind, body in (("Pod", spec), ("Deployment", {"template": {"spec": spec}}),
                                   ("CronJob", {"jobTemplate": {"spec": {"template": {"spec": spec}}}})):
                    with self.subTest(field=field, kind=kind, ports=ports):
                        self.assertEqual(expected, admitted(POLICIES[1], {"kind": kind, "spec": body}))


if __name__ == "__main__":
    unittest.main()

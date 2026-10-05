"""Validate live-check case construction offline; never invoke kubectl."""

import copy
import runpy
import unittest

from tests.test_templates import ROOT, rendered


class LiveCaseConstruction(unittest.TestCase):
    def test_core_invariants_have_schema_shaped_negative_objects(self):
        module = runpy.run_path(str(ROOT / "tests/live/check_admission.py"))
        source = [
            obj for _, entity, obj in rendered() if entity.get("USER_SLUG") == "ana"
        ]
        sts = next(obj for obj in source if obj["kind"] == "StatefulSet")
        pod = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                **copy.deepcopy(sts["spec"]["template"]["metadata"]),
                "name": "admission-positive",
                "namespace": "aa-u-ana",
                "ownerReferences": [
                    {
                        "apiVersion": "apps/v1",
                        "kind": "StatefulSet",
                        "name": sts["metadata"]["name"],
                        "uid": "synthetic",
                        "controller": True,
                    }
                ],
            },
            "spec": copy.deepcopy(sts["spec"]["template"]["spec"]),
        }
        before = copy.deepcopy(pod)
        cases = module["additional_denials"](pod, sts, source)
        self.assertEqual(pod, before)
        names = {name for name, _, _ in cases}
        self.assertTrue(
            {
                "supervisor wrong image",
                "supervisor mismatched uid",
                "supervisor mismatched pod gid",
                "supervisor default container",
                "shared PID namespace",
                "supervisor added capability",
                "CLI private control mount",
                "CLI supervisor token mount",
                "supervisor full login mount",
                "supervisor writable transcript",
                "supervisor TCP listener",
                "privileged",
                "privilege escalation",
                "writable root",
                "hostPath",
                "automounted API token",
                "wrong service account",
                "API token audience",
                "excessive token TTL",
                "estate login mount",
                "init login mount",
                "usage full home mount",
                "wrong tool claim",
                "omitted RAM medium",
                "command override",
                "bad StatefulSet name",
                "wrong login storage class",
                "wrong PVC user label",
                "PVC clone dataSource",
                "Job",
                "CronJob",
                "Service",
            }
            <= names
        )
        for name, obj, allowed in cases:
            with self.subTest(name=name):
                self.assertFalse(allowed)
                self.assertIn("apiVersion", obj)
                self.assertIn("kind", obj)
                self.assertEqual(obj["metadata"]["namespace"], "aa-u-ana")
        clone = next(obj for name, obj, _ in cases if name == "PVC clone dataSource")
        self.assertEqual(clone["spec"]["dataSource"]["kind"], "PersistentVolumeClaim")

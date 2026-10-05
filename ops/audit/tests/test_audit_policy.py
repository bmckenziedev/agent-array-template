#!/usr/bin/env python3
"""Tests for ops/maintenance/audit/audit-policy.yaml and lib/audit_policy_check.py.

Run: python3 ops/maintenance/tests/test_audit_policy.py   (needs PyYAML)

Checks the shipped policy passes every expectation, and that the checker itself catches
the mistakes that matter: a policy that logs Secret bodies, a rule order that lets a
broad rule shadow the Metadata-only rule, and files kube-apiserver would refuse.
"""
import copy
import importlib.util
import io
import os
import sys
import unittest
from contextlib import redirect_stdout

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
POLICY = os.path.join(HERE, "..", "audit-policy.tmpl.yaml")
spec = importlib.util.spec_from_file_location("apc", os.path.join(HERE, "..", "audit_policy_check.py"))
apc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(apc)


def load():
    with open(POLICY, encoding="utf-8") as f:
        return yaml.safe_load(f)


def failures(policy):
    with redirect_stdout(io.StringIO()):
        return apc.check(policy, quiet=True)


class PolicyTests(unittest.TestCase):
    def test_shipped_policy_is_valid_and_meets_every_expectation(self):
        p = load()
        apc.validate(p)
        self.assertEqual(failures(p), 0)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(apc.main([POLICY]), 0)

    def test_connect_keeps_metadata_and_stream_start(self):
        p = load()
        self.assertNotIn('ResponseStarted', p.get('omitStages', []))
        for sub in ['exec', 'attach', 'portforward']:
            for verb in ['get', 'create', 'connect']:
                self.assertEqual(apc.level_for(p, apc.R(apc.ADMIN, verb, 'pods', sub=sub)), 'Metadata')

    def test_vap_audit_request_records_envelope(self):
        p = load()
        self.assertEqual(apc.level_for(p, apc.R(apc.ADMIN, 'create', 'pods', ns='aa-u-ana')), 'Metadata')

    def test_request_level_for_secrets_is_caught(self):
        p = load()
        p["rules"].insert(0, {"level": "Request", "resources": [{"group": "", "resources": ["secrets"]}]})
        self.assertGreater(failures(p), 0)

    def test_catch_all_request_before_secret_rule_is_caught(self):
        p = load()
        p["rules"].insert(0, {"level": "RequestResponse", "verbs": ["create", "update", "patch"]})
        self.assertGreater(failures(p), 0)

    def test_dropping_the_exec_rule_is_caught(self):
        p = load()
        p["rules"] = [r for r in p["rules"] if "pods/exec" not in str(r.get("resources"))]
        self.assertGreater(failures(p), 0)

    def test_invalid_files_are_rejected(self):
        bad = []
        p = copy.deepcopy(load()); p["rules"][0]["level"] = "Everything"; bad.append(p)
        p = copy.deepcopy(load()); p["rules"][0]["nonResourceURLs"] = ["/he*lthz"]; bad.append(p)
        p = copy.deepcopy(load()); p["rules"][1]["nonResourceURLs"] = ["/x"]; bad.append(p)
        p = copy.deepcopy(load()); p["apiVersion"] = "audit.k8s.io/v1beta1"; bad.append(p)
        p = copy.deepcopy(load()); p["rules"][1]["resource"] = []; bad.append(p)
        for p in bad:
            with self.assertRaises(apc.Invalid):
                apc.validate(p)

    def test_subresource_matching_semantics(self):
        rule_policy = {"rules": [{"level": "Request", "resources": [{"group": "", "resources": ["pods"]}]}]}
        exec_req = apc.R(apc.ADMIN, "create", "pods", sub="exec")
        self.assertEqual(apc.level_for(rule_policy, exec_req), "None")   # "pods" != "pods/exec"
        rule_policy["rules"][0]["resources"][0]["resources"] = ["*/exec"]
        self.assertEqual(apc.level_for(rule_policy, exec_req), "Request")
        rule_policy["rules"][0]["resources"][0]["resources"] = ["pods/*"]
        self.assertEqual(apc.level_for(rule_policy, exec_req), "Request")


if __name__ == "__main__":
    unittest.main(verbosity=2)

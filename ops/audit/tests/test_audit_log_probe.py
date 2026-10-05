#!/usr/bin/env python3
"""Tests for ops/audit/audit_log_probe.py with synthetic audit events.

Run: python3 ops/maintenance/tests/test_audit_log_probe.py   (stdlib only)
"""
import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("probe", os.path.join(HERE, "..", "audit_log_probe.py"))
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

P = "aa-maint-audit-probe-1234"
T = "2025-01-02T10:00:05.000000Z"


def ev(verb, resource, level, name=P, sub="", group="", ns="default", req=False, resp=False, user="system:admin"):
    e = {"kind": "Event", "apiVersion": "audit.k8s.io/v1", "level": level, "stage": "ResponseComplete",
         "verb": verb, "user": {"username": user},
         "objectRef": {"resource": resource, "namespace": ns, "name": name, "apiGroup": group},
         "requestReceivedTimestamp": T, "stageTimestamp": T}
    if sub:
        e["objectRef"]["subresource"] = sub
    if req:
        e["requestObject"] = {"kind": "x"}
    if resp:
        e["responseObject"] = {"kind": "x"}
    return e


GOOD = [
    ev("create", "configmaps", "Metadata"),
    ev("delete", "configmaps", "Metadata"),
    ev("create", "roles", "Request", group="rbac.authorization.k8s.io", req=True),
    ev("delete", "roles", "Request", group="rbac.authorization.k8s.io", req=True),
    ev("create", "pods", "Metadata", sub="exec"),
    ev("list", "secrets", "Metadata", name=""),
]


def run(events, since="1735689600"):
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".log") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
        f.write("not json\n")
        path = f.name
    try:
        out = io.StringIO()
        with redirect_stdout(out):
            rc = probe.main([path, P, since])
        return rc, out.getvalue()
    finally:
        os.unlink(path)


class ProbeTests(unittest.TestCase):
    def test_good_log_passes(self):
        rc, out = run(GOOD)
        self.assertEqual(rc, 0, out)

    def test_secret_body_fails_invariant(self):
        rc, out = run(GOOD + [ev("create", "secrets", "Request", name="x", req=True)])
        self.assertEqual(rc, 1)
        self.assertIn("invariant", out)

    def test_configmap_at_request_level_fails(self):
        bad = [ev("create", "configmaps", "Request", req=True)] + GOOD[1:]
        rc, out = run(bad)
        self.assertEqual(rc, 1, out)

    def test_connect_body_is_refused(self):
        bad = [event for event in GOOD if event['objectRef'].get('subresource') != 'exec']
        bad.append(ev('create', 'pods', 'Metadata', sub='exec', req=True))
        self.assertEqual(run(bad)[0], 1)

    def test_missing_exec_event_fails(self):
        rc, out = run([e for e in GOOD if e["objectRef"].get("subresource") != "exec"])
        self.assertEqual(rc, 1, out)

    def test_events_before_since_are_ignored(self):
        rc, _ = run(GOOD, since="1891280000")
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

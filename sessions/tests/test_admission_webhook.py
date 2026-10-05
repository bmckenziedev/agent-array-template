"""Lookup admission tests use an in-memory API object graph, never a cluster."""

import copy
import json
import unittest

from tests.test_policy_merge import load


class AdmissionWebhook(unittest.TestCase):
    def setUp(self):
        self.module = load("aa-admission-webhook")
        self.prefix = "agent-array.example.org"
        self.namespace = "aa-u-ana"

        def labels(**values):
            return {self.prefix + "/" + key: value for key, value in values.items()}

        self.ns = {
            "metadata": {
                "labels": labels(kind="user-sessions", user="ana"),
                "annotations": {self.prefix + "/oidc-sub": "subject-ana"},
            }
        }
        self.pv = {
            "metadata": {
                "name": "aa-u-ana-claude-home-node-a",
                "labels": labels(kind="user-login", user="ana", tool="claude"),
            },
            "spec": {
                "claimRef": {"namespace": self.namespace, "name": "claude-home-node-a"},
                "storageClassName": "login",
                "persistentVolumeReclaimPolicy": "Retain",
                "local": {"path": "/var/lib/logins/ana/claude/node-a"},
                "nodeAffinity": {
                    "required": {
                        "nodeSelectorTerms": [
                            {
                                "matchExpressions": [
                                    {
                                        "key": "kubernetes.io/hostname",
                                        "operator": "In",
                                        "values": ["node-a"],
                                    }
                                ]
                            }
                        ]
                    }
                },
            },
        }
        self.pvc = {
            "metadata": {
                "name": "claude-home-node-a",
                "labels": labels(user="ana", tool="claude"),
            },
            "spec": {
                "storageClassName": "login",
                "volumeName": self.pv["metadata"]["name"],
            },
        }
        self.pod = {
            "metadata": {
                "name": "claude-node-a-0",
                "uid": "synthetic-pod",
                "labels": labels(user="ana", tool="claude"),
                "ownerReferences": [
                    {"kind": "StatefulSet", "controller": True, "name": "claude-node-a"}
                ],
            },
            "spec": {
                "nodeSelector": {"kubernetes.io/hostname": "node-a"},
                "volumes": [
                    {
                        "name": "home",
                        "persistentVolumeClaim": {
                            "claimName": self.pvc["metadata"]["name"]
                        },
                    }
                ],
            },
        }
        self.objects = {
            "/api/v1/namespaces/" + self.namespace: self.ns,
            "/api/v1/persistentvolumes/" + self.pv["metadata"]["name"]: self.pv,
            "/api/v1/namespaces/"
            + self.namespace
            + "/persistentvolumeclaims/claude-home-node-a": self.pvc,
            "/api/v1/namespaces/" + self.namespace + "/pods/claude-node-a-0": self.pod,
        }
        self.verifier = self.module.Verifier(
            lambda path: self.objects[path],
            self.prefix,
            "oidc:",
            "oidc:breakglass",
            "login",
            "/var/lib/logins",
        )
        self.events = []

    def request(
        self,
        resource="pods",
        operation="CREATE",
        obj=None,
        username="oidc:subject-ana",
        groups=None,
    ):
        return {
            "uid": "synthetic-request",
            "resource": {"resource": resource},
            "namespace": self.namespace,
            "name": self.pod["metadata"]["name"],
            "operation": operation,
            "subResource": "exec" if operation == "CONNECT" else "",
            "object": obj if obj is not None else self.pod,
            "userInfo": {"username": username, "groups": groups or []},
        }

    def review(self, request):
        return self.module.review(
            {"request": request}, self.verifier, self.events.append
        )["response"]

    def test_valid_holder_pod_and_exec_are_admitted(self):
        self.assertTrue(self.review(self.request())["allowed"])
        self.assertTrue(self.review(self.request(operation="CONNECT"))["allowed"])

    def test_unlabelled_namespace_and_missing_lookups_fail_closed(self):
        self.ns["metadata"]["labels"][self.prefix + "/kind"] = "system"
        self.assertFalse(self.review(self.request())["allowed"])
        self.objects.clear()
        self.assertFalse(self.review(self.request())["allowed"])

    def test_cross_user_tool_and_home_node_mounts_are_refused(self):
        mutations = [
            lambda: self.pvc["metadata"]["labels"].update(
                {self.prefix + "/user": "bo"}
            ),
            lambda: self.pvc["metadata"]["labels"].update(
                {self.prefix + "/tool": "codex"}
            ),
            lambda: self.pod["spec"]["nodeSelector"].update(
                {"kubernetes.io/hostname": "node-b"}
            ),
            lambda: self.pv["spec"]["claimRef"].update(namespace="aa-u-bo"),
        ]
        for mutation in mutations:
            self.setUp()
            mutation()
            self.assertFalse(self.review(self.request())["allowed"])

    def test_pv_requires_retention_exact_affinity_and_safe_path(self):
        mutations = [
            lambda: self.pv["spec"].update(persistentVolumeReclaimPolicy="Delete"),
            lambda: self.pv["spec"].update(nodeAffinity={}),
            lambda: self.pv["spec"]["local"].update(
                path="/var/lib/logins/ana/claude/../node-a"
            ),
            lambda: self.pv["spec"]["nodeAffinity"]["required"]["nodeSelectorTerms"][0][
                "matchExpressions"
            ][0].update(values=["node-a", "node-b"]),
        ]
        for mutation in mutations:
            self.setUp()
            mutation()
            self.assertFalse(
                self.review(self.request(resource="persistentvolumes", obj=self.pv))[
                    "allowed"
                ]
            )

    def test_pv_claim_reassignment_is_refused(self):
        request = self.request(
            resource="persistentvolumes", operation="UPDATE", obj=self.pv
        )
        request["oldObject"] = copy.deepcopy(self.pv)
        request["oldObject"]["spec"]["claimRef"]["namespace"] = "aa-u-bo"
        self.assertFalse(self.review(request)["allowed"])

    def test_breakglass_requires_group_and_ticket_on_target_pod(self):
        self.assertFalse(
            self.review(self.request(operation="CONNECT", username="oidc:admin"))[
                "allowed"
            ]
        )
        self.assertFalse(
            self.review(
                self.request(
                    operation="CONNECT",
                    username="oidc:admin",
                    groups=["oidc:breakglass"],
                )
            )["allowed"]
        )
        self.pod["metadata"]["annotations"] = {
            self.prefix + "/breakglass-ticket": "INC-synthetic"
        }
        self.assertFalse(
            self.review(self.request(operation="CONNECT", username="oidc:admin"))[
                "allowed"
            ]
        )
        self.assertTrue(
            self.review(
                self.request(
                    operation="CONNECT",
                    username="oidc:admin",
                    groups=["oidc:breakglass"],
                )
            )["allowed"]
        )
        self.assertEqual(self.events[-1]["detail"]["ticket"], "INC-synthetic")

    def test_missing_uid_and_audit_failure_deny(self):
        request = self.request()
        request.pop("uid")
        self.assertFalse(self.review(request)["allowed"])

        def unavailable(event):
            raise OSError("synthetic audit sink failure")

        result = self.module.review(
            {"request": self.request()}, self.verifier, unavailable
        )
        self.assertFalse(result["response"]["allowed"])

    def test_audit_omits_exec_commands_tokens_and_payloads(self):
        request = self.request(operation="CONNECT")
        request["object"] = {
            "command": ["synthetic confidential command"],
            "token": "synthetic confidential token",
        }
        self.assertTrue(self.review(request)["allowed"])
        encoded = json.dumps(self.events)
        self.assertNotIn("synthetic confidential", encoded)

"""Fake-kubectl tests for identity, confinement and server policy integration."""

import contextlib
import io
import json
import os
import shutil
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aa_cli import cli, config, kube, panel

ROOT = Path(__file__).resolve().parents[1]


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / "tests")
        self.root = Path(self.tmp.name)
        self.cfg = {
            "cluster_api_url": "https://cluster.example.org", "ca_bundle_path": "~/cluster-ca.pem",
            "oidc_issuer": "https://identity.example.org", "oidc_client_id": "aa-public",
            "label_prefix": "agent-array.example.org", "user_namespace_prefix": "aa-u-",
            "NS_SYSTEM": "aa-system", "panel_url": "", "pace_service_path":
            "/api/v1/namespaces/aa-system/services/pace:8080/proxy",
        }
        self.env = patch.dict(os.environ, {
            "AA_CONFIG_DIR": str(self.root / "config"), "AA_FAKE_STATE": str(self.root / "state.json"),
            "AA_FAKE_CALLS": str(self.root / "calls.jsonl"),
            "PATH": str(self.root) + os.pathsep + os.environ["PATH"],
        })
        self.env.start()
        script = Path(__file__).with_name("fake_kubectl.py")
        if os.name == "nt":
            (self.root / "kubectl.cmd").write_text(f'@echo off\n"{sys.executable}" "{script}" %*\n')
        else:
            import shlex
            launcher = self.root / "kubectl"
            launcher.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(script))} "$@"\n')
            launcher.chmod(0o755)
        user = {"slug": "ana", "oidc_sub": "subject-ana", "status": "active", "teams": ["payments"],
                "primary_team": "payments", "tools": {"claude": {"account": "ana-seat"}}}
        accounts = [{"id": "ana-seat", "holder": "ana", "type": "seat", "vendor": "anthropic",
                     "max_concurrent_sessions": 3, "windows": ["5h", "weekly"]},
                    {"id": "bo-seat", "holder": "bo", "type": "seat", "vendor": "anthropic"},
                    {"id": "payments-api", "owner_team": "payments", "type": "api", "vendor": "anthropic"}]
        teams = [{"id": "payments", "session_tier": "standard", "data_classes_allowed": ["internal", "restricted"], "vendors_allowed": {
            "internal": ["anthropic"], "restricted": ["local"]}}]
        self.state = {
            "subject": "oidc:subject-ana", "configmaps": {
                "org-directory": {"users.json": json.dumps([user]), "teams.json": json.dumps(teams),
                                  "accounts.json": json.dumps(accounts)},
                "aa-client-directory": {"identities.json": json.dumps({"oidc:subject-ana": {
                    "slug": "ana", "oidc_sub": "subject-ana", "namespace": "aa-u-ana",
                    "max_replicas_per_tool": 1}})},
                "context-policy": {"estates.json": json.dumps([{"id": "payments", "data_class": "internal",
                    "repos": ["github.com/example-org/payments-api"], "snapshot_targets": ["claude"],
                    "deny_globs": ["*.key"]}])},
            }, "namespace": {"metadata": {"name": "aa-u-ana", "labels": {
                "agent-array.example.org/user": "ana", "agent-array.example.org/kind": "user-sessions"}}},
            "pods": [self.obj("a-changing-name", "claude", phase="Running"),
                     self.obj("another-user", "claude", user="bo", phase="Running")],
            "statefulsets": [self.obj("a-changing-workload", "claude", replicas=0)],
            "pace": accounts,
        }
        config.write_json(config.directory() / "config.json", self.cfg)
        self.save()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def obj(self, name, tool, user="ana", phase=None, replicas=None):
        obj = {"metadata": {"name": name, "namespace": "aa-u-" + user, "resourceVersion": "12", "labels": {
            "agent-array.example.org/user": user, "agent-array.example.org/tool": tool}}}
        if phase:
            obj["status"] = {"phase": phase}
        if replicas is not None:
            obj["spec"] = {"replicas": replicas}
        return obj

    def save(self):
        (self.root / "state.json").write_text(json.dumps(self.state))

    def calls(self):
        return [json.loads(line) for line in (self.root / "calls.jsonl").read_text().splitlines()]

    def invoke(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(list(args))
        return rc, out.getvalue(), err.getvalue()

    def test_init_exact_fields_only(self):
        source = self.root / "client.json"
        source.write_text(json.dumps(self.cfg))
        self.assertEqual(self.invoke("init", "--from", str(source))[0], 0)
        source.write_text(json.dumps(dict(self.cfg, token="example-value")))
        self.assertEqual(self.invoke("init", "--from", str(source))[0], 2)

    def test_login_exec_kubeconfig_no_credentials(self):
        self.assertEqual(self.invoke("login")[0], 0)
        data = json.loads(config.kubeconfig().read_text())
        auth = data["users"][0]["user"]
        self.assertEqual(set(auth), {"exec"})
        self.assertIn("--grant-type=device-code", auth["exec"]["args"])
        self.assertEqual(data["clusters"][0]["cluster"]["server"], self.cfg["cluster_api_url"])
        self.assertTrue(any("whoami" in a for a in self.calls()))

    def test_missing_login_plugin_prints_install_hint(self):
        self.state["plugin"] = False
        self.save()
        rc, _, err = self.invoke("login")
        self.assertEqual(rc, 2)
        self.assertIn("krew install oidc-login", err)
        self.assertFalse(config.kubeconfig().exists())

    def test_whoami_directory_identity(self):
        rc, out, _ = self.invoke("whoami")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["namespace"], "aa-u-ana")
        self.assertEqual(json.loads(out)["accounts"], ["ana-seat"])

    def test_label_discovery_no_pod_constants(self):
        rc, out, _ = self.invoke("sessions", "list")
        self.assertEqual(rc, 0)
        self.assertEqual([r["pod"] for r in json.loads(out)], ["a-changing-name"])
        self.assertIn("agent-array.example.org/user=ana,agent-array.example.org/tool=claude", self.calls()[-1])

    def test_cross_namespace_refused_before_session_request(self):
        rc, _, err = self.invoke("sessions", "logs", "--tool", "claude", "-n", "aa-u-bo")
        self.assertEqual(rc, 2)
        self.assertIn("namespace confinement", err)
        self.assertFalse(any("logs" in c for c in self.calls()))

    def test_namespace_is_derived_without_namespace_reads(self):
        self.state["namespace"]["metadata"]["labels"]["agent-array.example.org/user"] = "bo"
        self.save()
        self.assertEqual(self.invoke("sessions", "list")[0], 0)
        self.assertFalse(any("namespace" in call for call in self.calls()))

    def test_unknown_oidc_refused(self):
        self.state["subject"] = "oidc:unregistered"
        self.save()
        self.assertEqual(self.invoke("whoami")[0], 2)

    def test_suspended_user_refused(self):
        users = json.loads(self.state["configmaps"]["org-directory"]["users.json"])
        users[0]["status"] = "suspended"
        self.state["configmaps"]["org-directory"]["users.json"] = json.dumps(users)
        self.save()
        self.assertEqual(self.invoke("whoami")[0], 2)

    def test_scale_tier_clamping(self):
        rc, out, _ = self.invoke("sessions", "scale", "3", "--tool", "claude")
        self.assertEqual(rc, 0)
        self.assertIn("replicas: 1", out)
        self.assertIn("--resource-version", self.calls()[-1])
        self.assertEqual(self.calls()[-1][-3:], ["1", "--resource-version", "12"])

    def test_scale_above_account_refused(self):
        self.assertEqual(self.invoke("sessions", "scale", "4", "--tool", "claude")[0], 2)
        self.assertFalse(any("scale" in a for a in self.calls()))

    def test_scale_aggregate_account_limit(self):
        self.state["statefulsets"].append(self.obj("another-workload", "claude", replicas=3))
        self.save()
        args = ["sessions", "scale", "1", "--tool", "claude", "--statefulset", "a-changing-workload"]
        self.assertEqual(self.invoke(*args)[0], 2)

    def test_unentitled_tool_refused(self):
        self.assertEqual(self.invoke("sessions", "logs", "--tool", "codex")[0], 2)

    def test_pace_service_proxy_and_filtered_accounts(self):
        rc, out, _ = self.invoke("pace", "status")
        self.assertEqual(rc, 0)
        self.assertEqual({a["id"] for a in json.loads(out)}, {"ana-seat", "payments-api"})
        self.assertEqual(self.calls()[-1][-1], self.cfg["pace_service_path"] + "/v1/accounts")

    def test_manual_usage_holder_exec(self):
        self.assertEqual(self.invoke("pace", "report", "--tool", "claude", "5h=40,weekly=22")[0], 0)
        self.assertEqual(self.calls()[-1][-3:], ["aa-usage-report", "--manual", "5h=40,weekly=22"])

    def test_manual_usage_invalid_values(self):
        for value in ("5h=101", "other=40", "5h=nan", "5h=2,5h=3"):
            self.assertEqual(self.invoke("pace", "report", "--tool", "claude", value)[0], 2)

    def test_panel_absent_refused(self):
        with patch.object(panel, "device_token") as token:
            self.assertEqual(self.invoke("get", "task-1", "--out", str(self.root / "out"))[0], 2)
            token.assert_not_called()

    def test_context_override_in_scoped_calls_refused(self):
        identity = kube.Identity(self.cfg)
        for args in (["get", "pods", "-A"], ["get", "pods", "--namespace=aa-u-bo"],
                     ["get", "pods", "--context=admin"]):
            with self.assertRaises(ValueError):
                identity.scoped(args)

    def test_policy_absent_fails_closed(self):
        del self.state["configmaps"]["context-policy"]
        self.save()
        with self.assertRaises(ValueError):
            cli.policy(kube.Identity(self.cfg), "payments")

    def repo(self):
        repo = self.root / "repo"
        repo.mkdir()
        def git(*args):
            subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
        git("init")
        git("config", "user.name", "Example User")
        git("config", "user.email", "user@example.org")
        git("remote", "add", "origin", "https://github.com/example-org/payments-api.git")
        (repo / "code.py").write_text("print('example')\n")
        git("add", ".")
        git("commit", "-m", "Synthetic fixture")
        return repo

    def test_work_and_snapshot_use_receiver_with_origin(self):
        repo = self.repo()
        for name, action in (("work", "up"), ("work", "push"), ("snapshot", "up")):
            rc, _, err = self.invoke(name, action, "--tool", "claude", "--estate", "payments",
                "--path", str(repo), "--ws", "example")
            self.assertEqual(rc, 0, err)
            call = self.calls()[-1]
            self.assertIn("aa-snapshot", call)
            self.assertEqual(call[-2:], ["--repo", "github.com/example-org/payments-api"])

    def test_estate_data_class_denies_before_transfer(self):
        estates = json.loads(self.state["configmaps"]["context-policy"]["estates.json"])
        estates[0]["data_class"] = "restricted"
        self.state["configmaps"]["context-policy"]["estates.json"] = json.dumps(estates)
        self.save()
        repo = self.repo()
        rc, _, err = self.invoke("snapshot", "up", "--tool", "claude", "--estate", "payments",
            "--path", str(repo), "--ws", "example")
        self.assertEqual(rc, 2)
        self.assertIn("data class", err)
        self.assertFalse(any("aa-snapshot" in call for call in self.calls()))


class PanelTests(unittest.TestCase):
    def test_bearer_header_only(self):
        cfg = {"panel_url": "https://panel.example.org"}
        with patch.object(panel, "device_token", return_value="opaque-example"), \
                patch.object(panel, "request", return_value=b"{}") as request:
            panel.Client(cfg).call("/api/tasks")
            headers = request.call_args.kwargs["headers"]
            self.assertEqual(headers["Authorization"], "Bearer opaque-example")
            self.assertEqual(set(headers), {"Authorization", "Content-Type"})

    def test_multipart_task_archive(self):
        cfg = {"panel_url": "https://panel.example.org"}
        with patch.object(panel, "device_token", return_value="opaque-example"), \
                patch.object(panel, "request", return_value=b'{"task_id":"task-1"}') as request:
            response = panel.Client(cfg).create_task(b"synthetic-archive", "payments", "review")
            self.assertEqual(response["task_id"], "task-1")
            self.assertIn(b'name="estate_id"', request.call_args.kwargs["data"])
            self.assertIn("multipart/form-data", request.call_args.kwargs["headers"]["Content-Type"])

    def test_device_flow_no_token_file(self):
        cfg = {"oidc_issuer": "https://identity.example.org", "oidc_client_id": "public"}
        discovery = {"issuer": cfg["oidc_issuer"], "device_authorization_endpoint": "https://identity.example.org/device",
                     "token_endpoint": "https://identity.example.org/token"}
        with patch.object(panel, "request", return_value=json.dumps(discovery).encode()), \
                patch.object(panel, "form", side_effect=[{"verification_uri": "https://identity.example.org/verify",
                    "user_code": "EXAMPLE", "device_code": "opaque", "expires_in": 30},
                    {"token_type": "Bearer", "access_token": "opaque-example"}]), \
                patch.object(panel.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(panel.device_token(cfg), "opaque-example")

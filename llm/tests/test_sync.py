import contextlib
import copy
import io
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.parse
from unittest.mock import patch

import teams_sync as sync
from tests.test_render import fixture, rendered


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.state = {"teams": [], "users": []}
        self.requests = []
        state, calls = self.state, self.requests

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, data, code=200):
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())

            def do_GET(self):
                kind = self.path.split("/")[1]
                calls.append(("GET", self.path, None))
                if kind == "failure":
                    self.respond({"key": "FAKE_SERVER_SECRET"}, 403)
                    return
                if kind == "redirect":
                    self.send_response(302)
                    self.send_header("Location", "/user/list")
                    self.end_headers()
                    return
                if kind == "user":
                    params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                    page = int(params.get("page", ["1"])[0])
                    size = int(params.get("page_size", ["100"])[0])
                    users = state["users"][(page - 1) * size:page * size]
                    self.respond({"users": users, "page": page, "page_size": size,
                                  "total": len(state["users"])})
                else:
                    self.respond(state[kind + "s"])

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append(("POST", self.path, payload))
                kind, operation = self.path.strip("/").split("/")
                if kind == "key":
                    self.respond({"key": "FAKE_PRIVATE_MINT_VALUE"})
                    return
                identity = kind + "_id"
                if operation.startswith("member_"):
                    team = next(t for t in state["teams"] if t["team_id"] == payload["team_id"])
                    members = team.setdefault("members_with_roles", [])
                    member = payload.get("member", payload)
                    members[:] = [m for m in members if m["user_id"] != member["user_id"]]
                    if operation != "member_delete":
                        members.append({"user_id": member["user_id"], "role": member["role"]})
                        members.sort(key=lambda m: m["user_id"])
                elif operation == "delete":
                    state[kind + "s"][:] = [i for i in state[kind + "s"] if i[identity] not in payload[kind + "_ids"]]
                else:
                    existing = next((i for i in state[kind + "s"] if i[identity] == payload[identity]), None)
                    clean = {k: v for k, v in payload.items() if k != "auto_create_key"}
                    if existing:
                        existing.update(clean)
                    else:
                        state[kind + "s"].append(clean)
                self.respond({"ok": True})

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.api = sync.API(f"http://127.0.0.1:{self.server.server_port}", "FAKE_MASTER_VALUE")
        self.desired = json.loads(rendered(fixture())["files/llm/teams.json"])

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()

    def test_plan_apply_idempotency(self):
        current = {k + "s": self.api.list_all(k) for k in ("team", "user")}
        actions = sync.plan(self.desired, current)
        self.assertTrue(actions)
        self.assertFalse(any(r[0] == "POST" for r in self.requests))
        sync.apply(self.api, actions)
        self.assertEqual(sync.plan(self.desired, self.state), [])
        self.assertTrue(all(r[2].get("auto_create_key") is False for r in self.requests if r[1] == "/user/new"))
        changed = copy.deepcopy(self.desired)
        changed["teams"][0]["max_budget"] = 100
        changed["teams"][0]["members_with_roles"] = []
        sync.apply(self.api, sync.plan(changed, self.state))
        self.assertEqual(sync.plan(changed, self.state), [])

    def test_deletes_guarded_and_unmanaged_preserved(self):
        self.state["users"] = [{"user_id": "stale", "metadata": {"managed_by": "llm"}},
                               {"user_id": "external", "metadata": {}}]
        empty = {"teams": [], "users": []}
        sync.apply(self.api, sync.plan(empty, self.state))
        self.assertEqual(len(self.state["users"]), 2)
        sync.apply(self.api, sync.plan(empty, self.state, True))
        self.assertEqual(self.state["users"][0]["user_id"], "external")

    def test_collision(self):
        self.state["users"] = [{"user_id": "ana", "metadata": {}}]
        with self.assertRaises(ValueError):
            sync.plan(self.desired, self.state)

    def test_paginated_user_list(self):
        self.state["users"] = [{"user_id": f"user-{i}", "metadata": {}} for i in range(105)]
        self.assertEqual(len(self.api.list_all("user")), 105)
        self.assertTrue(any("page=2" in r[1] for r in self.requests))

    def test_error_body_secret_is_suppressed(self):
        with self.assertRaises(ValueError) as error:
            self.api.request("GET", "/failure")
        self.assertNotIn("FAKE_SERVER_SECRET", str(error.exception))
        self.assertIn("403", str(error.exception))

    def test_admin_redirect_is_rejected(self):
        with self.assertRaises(ValueError):
            self.api.request("GET", "/redirect")
        self.assertFalse(any(r[1] == "/user/list" for r in self.requests))

    def test_cli_secrets_and_apply_guard(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "teams.json"
            path.write_text(json.dumps(self.desired))
            args = ["--base-url", self.api.base, "--file", str(path)]
            out = io.StringIO()
            with patch.dict(os.environ, {"LITELLM_MASTER_KEY": "FAKE_MASTER_VALUE"}), contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                self.assertEqual(sync.main(args + ["plan", "--check-secrets"]), 0)
                self.assertEqual(sync.main(args + ["apply"]), 1)
                self.assertEqual(sync.main(args + ["apply", "--yes"]), 0)
            self.assertNotIn("FAKE_MASTER_VALUE", out.getvalue())
            self.assertIn("llm-provider-openai-shared", out.getvalue())

    def test_mint_key_only_sent_to_sealer(self):
        output = io.StringIO()
        with patch.dict(os.environ, {"LITELLM_MASTER_KEY": "FAKE_MASTER_VALUE"}), patch.object(sync.subprocess, "run") as run, contextlib.redirect_stdout(output):
            run.return_value.returncode = 0
            self.assertEqual(sync.main(["--base-url", self.api.base, "mint-key-for", "portal",
                                        "--seal-command", "fake-seal"]), 0)
        self.assertNotIn("FAKE_PRIVATE_MINT_VALUE", output.getvalue())
        self.assertEqual(run.call_args.kwargs["input"], b"FAKE_PRIVATE_MINT_VALUE")
        payload = next(r[2] for r in self.requests if r[1] == "/key/generate")
        self.assertNotIn("key_type", payload)
        self.assertEqual(payload["allowed_routes"], ["/key/generate", "/key/delete", "/key/info"])

    def test_usage_is_account_scoped_and_excludes_seats(self):
        api = sync.API("http://127.0.0.1:1", "synthetic")
        from unittest.mock import Mock
        api.list_all = Mock(return_value=[{"metadata": {"account_id": "api-a"}, "spend": 25},
                                         {"metadata": {"account_id": "api-b"}, "spend": 90}])
        pace = Mock()
        sync.report_usage(api, pace, [{"id": "api-a", "type": "api", "budget_usd_per_month": 100, "windows": ["monthly"]},
                                    {"id": "seat-a", "type": "seat", "windows": ["weekly"]}])
        body = pace.request.call_args.args[2]
        self.assertEqual(body["account_id"], "api-a")
        self.assertEqual(body["used_pct"], 25)
        self.assertEqual(body["source"], "litellm")
        pace.request.assert_called_once()

    def test_seal_failure_revokes_key(self):
        output = io.StringIO()
        with patch.dict(os.environ, {"LITELLM_MASTER_KEY": "FAKE_MASTER_VALUE"}), patch.object(sync.subprocess, "run") as run, contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            run.return_value.returncode = 1
            self.assertEqual(sync.main(["--base-url", self.api.base, "mint-key-for", "farm-mcp",
                                        "--seal-command", "fake-seal"]), 1)
        self.assertTrue(any(r[1] == "/key/delete" for r in self.requests))
        self.assertNotIn("FAKE_PRIVATE_MINT_VALUE", output.getvalue())

    def test_sealer_missing_revokes_key(self):
        output = io.StringIO()
        with patch.dict(os.environ, {"LITELLM_MASTER_KEY": "FAKE_MASTER_VALUE"}), patch.object(sync.subprocess, "run", side_effect=FileNotFoundError), contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            self.assertEqual(sync.main(["--base-url", self.api.base, "mint-key-for", "portal",
                                        "--seal-command", "missing-seal"]), 1)
        self.assertTrue(any(r[1] == "/key/delete" for r in self.requests))
        self.assertNotIn("FAKE_PRIVATE_MINT_VALUE", output.getvalue())

    def test_admin_bundle_stays_inside_gateway(self):
        output = rendered(fixture())
        admin = json.loads(output["global/llm/k8s/litellm-admin.yaml"])
        self.assertEqual(admin["data"]["teams.json"], output["files/llm/teams.json"])
        deploy = json.loads(output["global/llm/k8s/deployment.yaml"])
        pod = deploy["spec"]["template"]["spec"]
        self.assertIn({"name": "admin", "configMap": {"name": "litellm-admin"}}, pod["volumes"])

    def test_repeated_mint_reuses_managed_identity(self):
        output = io.StringIO()
        with patch.dict(os.environ, {"LITELLM_MASTER_KEY": "FAKE_MASTER_VALUE"}), patch.object(sync.subprocess, "run") as run, contextlib.redirect_stdout(output):
            run.return_value.returncode = 0
            argv = ["--base-url", self.api.base, "mint-key-for", "portal", "--seal-command", "fake-seal"]
            self.assertEqual(sync.main(argv), 0)
            self.assertEqual(sync.main(argv), 0)
        self.assertEqual(len([r for r in self.requests if r[1] == "/user/new"]), 1)

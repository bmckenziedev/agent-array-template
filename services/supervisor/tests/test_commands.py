"""Command paths use fake CLIs and bounded in-process HTTP receivers."""
import contextlib
import datetime
import http.server
import json
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from aa_supervisor.connector import Connector
from aa_supervisor.egress import Egress
from aa_supervisor.pace import Pace, LeaseDenied
from aa_supervisor.service import Supervisor, PluginContext
from .test_supervisor import policy, HOLDER, FakeTmux


@contextlib.contextmanager
def server(handler):
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(httpd.server_port)
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


class Commands(unittest.TestCase):
    def setUp(self):
        self.captured = []
        self.tmux = FakeTmux()
        self.service = Supervisor({"AA_USER": "ana", "AA_ACCOUNT": "seat", "AA_MAX_SESSIONS": "10"},
                                  Egress(sink=self.captured.append), self.tmux)
        self.service.policy = policy()
        self.reload = patch.object(self.service, "reload")
        self.reload.start()
        self.addCleanup(self.reload.stop)

    def execute(self, command, **fields):
        return self.service.execute({"command": command, "actor": HOLDER, **fields})

    def test_policy_unavailable_fail_closed_recovery_commands(self):
        self.service.policy = {}
        self.assertEqual(self.service.execute({"command": "sessions.spawn"}, local=True)["error"], "policy_unavailable")
        self.assertIn("sessions", self.service.execute({"command": "sessions.list"}, local=True))

    def test_connector_cannot_claim_local_holder(self):
        for command in ["sessions.spawn", "sessions.input", "permission.decide"]:
            result = self.service.execute({"command": command, "actor": {"kind": "local"}})
            self.assertEqual(result["error"], "forbidden")

    def test_control_permission_verb_separation(self):
        from aa_supervisor.transport import UnixAPI
        api = UnixAPI(self.service)
        for command in ["sessions.list", "sessions.spawn", "sessions.stop", "permission.decide"]:
            self.assertEqual(api.dispatch({"command": command}, 1)["error"], "forbidden")
        self.assertEqual(api.dispatch({"command": "permission.request"}, 0)["error"], "forbidden")
        api.selector.close()

    def test_refusal_before_lease(self):
        self.service.policy["spawn_refused_accounts"] = ["seat"]
        with patch.object(self.service.pace, "acquire") as lease:
            self.assertEqual(self.execute("sessions.spawn", tool="claude", brief="brief")["error"], "account_refused")
            lease.assert_not_called()

    def test_pod_cap_before_lease(self):
        self.service.env["AA_MAX_SESSIONS"] = "1"
        with patch.object(self.service.pace, "acquire") as lease:
            self.assertEqual(self.execute("sessions.spawn", tool="claude", brief="brief")["error"], "rate_limited")
            lease.assert_not_called()

    def test_unsupported_kimi(self):
        self.assertEqual(self.execute("sessions.spawn", tool="kimi", brief="brief")["error"], "unsupported_tool")

    def test_unverified_launch_is_closed(self):
        self.assertEqual(self.execute("sessions.spawn", tool="claude", brief="brief")["error"], "not_drivable")

    def fake_launch(self, tool, cwd, brief, *args):
        record = dict(self.service.registry.base(tool), id="spawn-id", kind="spawned", pane="%3", window="@3",
            cwd=cwd, state="running", drivable="full", drive_via="handle", directory="unused")
        self.service.registry.spawned[record["id"]] = record
        return record

    def test_spawn_fake_cli_lease_and_advisory(self):
        self.service.verified.update(claude=True, tmux_argv=True)
        with patch.object(self.service.pace, "advisory", return_value=[{"reason": "near_cap"}]), \
             patch.object(self.service.pace, "acquire", return_value={"lease_id": "lease-a", "expires_at": "future"}), \
             patch.object(self.service.registry, "launch", side_effect=self.fake_launch) as launch:
            result = self.execute("sessions.spawn", tool="claude", brief="private brief", cwd="/work")
            self.assertEqual(result["id"], "spawn-id")
            self.assertEqual(result["advisories"], [{"reason": "near_cap"}])
            self.assertEqual(result["lease"]["lease_id"], "lease-a")
            self.assertNotIn("private brief", str(self.tmux.calls))
            self.assertEqual(launch.call_args.args[2], "private brief")

    def test_lease_409_unreachable_fail_open_and_closed(self):
        self.service.verified.update(claude=True, tmux_argv=True)
        with patch.object(self.service.pace, "advisory", return_value=[]), \
             patch.object(self.service.pace, "acquire", side_effect=LeaseDenied("cap")):
            self.assertEqual(self.execute("sessions.spawn", tool="claude", brief="brief")["error"], "lease_denied")
        for closed in [False, True]:
            self.service.env["AA_LEASE_FAIL_CLOSED"] = str(closed).lower()
            with patch.object(self.service.pace, "advisory", side_effect=OSError()), \
                 patch.object(self.service.pace, "acquire", side_effect=OSError()), \
                 patch.object(self.service.registry, "launch", side_effect=self.fake_launch):
                result = self.execute("sessions.spawn", tool="claude", brief="brief")
                if closed:
                    self.assertEqual(result["error"], "pace_unreachable")
                else:
                    self.assertEqual(result["advisories"][0]["reason"], "pace_unreachable")

    def test_stop_governor_reason_and_emergency(self):
        self.service.policy.update(stop_grace_s=0, stops_per_minute_session=1)
        self.assertEqual(self.execute("sessions.stop", id="pane-1")["error"], "gate_rejected")
        self.assertTrue(self.execute("sessions.interrupt", id="pane-1", reason="holder request")["ok"])
        self.assertEqual(self.execute("sessions.stop", id="pane-1", reason="holder request")["error"], "rate_limited")
        self.assertTrue(self.execute("sessions.stop", id="pane-1", reason="emergency", emergency=True)["ok"])
        self.assertIn("supervisor.stop_emergency", [json.loads(v)["event"] for v in self.captured])

    def test_input_gate_rejection_burst_notifies(self):
        with patch.object(self.service, "notify") as notify:
            for _ in range(5):
                self.assertEqual(self.execute("sessions.input", id="pane-1", text="\x01")["error"], "gate_rejected")
            notify.assert_called_with({"type": "input.rejected_burst"})

    def test_signal_only_and_none_not_drivable(self):
        self.service.policy["drive_discovered"] = False
        self.assertEqual(self.execute("sessions.input", id="pane-1", text="hello")["error"], "not_drivable")
        record = dict(self.service.registry.base("claude"), id="headless", kind="headless", pane=None,
                      window=None, state="running", cwd=None, drivable="none", drive_via="none")
        with patch.object(self.service.registry, "discover", return_value={"headless": record}):
            for command in ["sessions.input", "sessions.stop", "sessions.interrupt"]:
                self.assertEqual(self.execute(command, id="headless", text="hello", reason="holder request")["error"], "not_drivable")

    def test_fake_notifier_receives_only_restricted_context(self):
        contexts = []
        class Plugin:
            def __init__(self, config, ctx): contexts.append(ctx)
            def notify(self, event): return {"delivered": True, "detail": "synthetic"}
        self.service.policy["notifiers"] = [{"type": "fake"}]
        with patch("importlib.import_module", return_value=types.SimpleNamespace(Plugin=Plugin)):
            self.service.notify({"type": "session.started"})
        self.assertEqual(PluginContext.__slots__, ("audit", "post_json"))
        self.assertFalse(hasattr(contexts[0], "env"))

    def permission_session(self):
        self.service.registry.spawned["spawn-id"] = dict(self.service.registry.base("claude"),
            id="spawn-id", kind="spawned", pane="%3", window="@3", state="running", cwd="/work",
            drivable="full", drive_via="handle", permission_routing="available")

    def request(self, tool, request_id="request-a"):
        return self.service.permission({"command": "permission.request", "session_id": "spawn-id",
            "request_id": request_id, "tool": tool, "arguments": {}, "summary": "synthetic summary"})

    def test_permission_auto_and_request_binding(self):
        self.assertEqual(self.request("read")["decision"], "deny")
        self.permission_session()
        self.assertEqual(self.request("read")["decision"], "allow")
        self.assertEqual(self.request("purchase")["decision"], "deny")

    def test_lease_renew_and_release_on_exit(self):
        record = self.fake_launch("claude", "/work", "brief")
        self.tmux.rows.append({"pane": "%3", "window": "@3", "command": "claude", "cwd": "/work", "name": "spawn"})
        self.service.leases["spawn-id"] = {"lease": {"lease_id": "lease-a"}, "renew_at": 0}
        with patch.object(self.service.registry, "output", return_value='{"type":"system","session_id":"cli-id"}\n'), \
             patch.object(self.service.pace, "renew") as renew, patch.object(self.service.pace, "release") as release:
            self.service.tick()
            renew.assert_called_once_with("lease-a")
            self.assertEqual(record["session_id"], "cli-id")
            self.tmux.rows.pop()
            self.service.tick()
            release.assert_called_once_with("lease-a")
            self.assertEqual(record["state"], "exited")

    def test_private_journal_restores_leases_without_starting_work(self):
        identity = "a" * 16 + "." + "b" * 16
        record = dict(self.service.registry.base("claude"), id=identity, kind="spawned", pane="%3", window="@3",
            cwd="/work", state="running", drivable="full", drive_via="handle",
            directory="/run/aa/spawn-" + "c" * 16 + "." + "d" * 16 + "/" + identity)
        self.service.restore_state({"sessions": {identity: record}, "seq": 7,
            "leases": {identity: {"lease": {"lease_id": "lease-a"}, "renew_at": 999}}})
        self.assertEqual(self.service.leases[identity]["renew_at"], 0)
        self.assertEqual(self.service.seq, 7)
        self.assertEqual(self.tmux.calls, [])
        self.assertEqual(record["drivable"], "signal-only")

    def test_permission_human_dedupe_decider_and_timeout(self):
        self.permission_session()
        with patch.object(self.service, "notify") as notify:
            first = self.request("purchase")
            self.request("purchase")
            self.assertEqual(sum(c.args[0]["type"] == "permission.human_needed" for c in notify.call_args_list), 1)
        self.assertEqual(first["decision"], "pending")
        actor = {"kind": "user", "id": "bo", "sub": "subject-bo"}
        cmd = {"command": "permission.decide", "actor": actor, "request_id": "request-a", "decision": "allow"}
        self.assertEqual(self.service.execute(cmd)["error"], "forbidden")
        self.service.policy["deciders"]["money"] = ["bo"]
        first["deadline"] = 0
        self.assertEqual(self.service.execute(cmd)["decision"], "deny")

    def test_policy_hook_allow_filtered_and_timeout_escalates(self):
        self.permission_session()
        self.service.policy["permission_tiers"]["money"] = "policy"
        self.service.policy["policy_hook_url"] = "https://relay.example.org/decision"
        with patch.object(self.service, "post_json", return_value={"decision": "allow"}):
            self.assertEqual(self.request("purchase", "one")["decision"], "pending")
            self.service.policy["policy_may_allow"] = ["money"]
            self.assertEqual(self.request("purchase", "two")["decision"], "allow")
        with patch.object(self.service, "post_json", side_effect=TimeoutError()):
            self.assertEqual(self.request("network", "three")["decision"], "pending")


class OutboundHTTP(unittest.TestCase):
    def test_pace_acquire_renew_release_409_and_advisory(self):
        calls = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                body = json.dumps([{"id": "seat", "windows": [{"name": "weekly", "used_pct": 75,
                    "cap_pct": 80, "resets_at": "future", "updated_at":
                    (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=2)).isoformat()}]}]).encode()
                self.send_response(200); self.end_headers(); self.wfile.write(body)
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                calls.append((self.path, payload, self.headers.get("Authorization")))
                code = 409 if payload.get("account_id") == "refused" else 200
                self.send_response(code); self.end_headers()
                self.wfile.write(json.dumps({"reason": "cap"} if code == 409 else {"lease_id": "lease-a", "expires_at": "future"}).encode())
            def do_DELETE(self):
                calls.append((self.path, None, self.headers.get("Authorization")))
                self.send_response(204); self.end_headers()
        with tempfile.TemporaryDirectory() as tmp, server(Handler) as url:
            token = Path(tmp) / "token"
            token.write_text("synthetic-one")
            env = {"AA_PACE_URL": url, "AA_ACCOUNT": "seat", "AA_POD_NAME": "pod-a", "AA_PACE_TOKEN_FILE": str(token)}
            pace = Pace(env, Egress(sink=lambda _: None))
            self.assertEqual(pace.advisory(policy())[0]["reason"], "near_cap")
            self.assertEqual(pace.acquire()["lease_id"], "lease-a")
            token.write_text("synthetic-two")
            pace.renew("lease-a")
            pace.release("lease-a")
            env["AA_ACCOUNT"] = "refused"
            with self.assertRaises(LeaseDenied): pace.acquire()
            self.assertEqual(calls[0][2], "Bearer synthetic-one")
            self.assertEqual(calls[1][2], "Bearer synthetic-two")

    def test_connector_hello_metrics_token_rotation_seq_and_backoff(self):
        posts = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                posts.append((payload, self.headers.get("Authorization")))
                self.send_response(200); self.end_headers(); self.wfile.write(b'{"ack_seq":2}')
            def do_GET(self):
                self.send_response(200); self.end_headers()
                self.wfile.write(b'data: {"command":"metrics","actor":{"kind":"automation","id":"observer"}}\n\n')
        with tempfile.TemporaryDirectory() as tmp, server(Handler) as url:
            token = Path(tmp) / "token"
            token.write_text("synthetic-one")
            service = Supervisor({"AA_USER": "ana", "AA_POD_NAME": "pod-a"}, Egress(sink=lambda _: None), FakeTmux())
            service.policy, service.seq = policy(), 2
            connector = Connector(service, url, str(token))
            connector.post(connector.hello())
            token.write_text("synthetic-two")
            connector.post({"type": "metrics"})
            self.assertEqual(posts[0][0]["type"], "hello")
            self.assertEqual(posts[0][1], "Bearer synthetic-one")
            self.assertEqual(posts[1][1], "Bearer synthetic-two")
            self.assertEqual(connector.hello()["resume_seq"], 2)
            with patch.object(service, "reload"), self.assertRaises(OSError): connector.run_once()
            self.assertTrue(any(p[0]["type"] == "metrics" for p in posts))
            self.assertTrue(any(p[0]["type"] == "response" for p in posts))
            self.assertFalse(connector.up)
            self.assertLessEqual(connector.backoff(100), 60)

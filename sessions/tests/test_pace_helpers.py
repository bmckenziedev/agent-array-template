import contextlib
import http.server
import io
import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_policy_merge import load


class PaceHelpers(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.token = self.root / "token"
        self.token.write_text("synthetic-token")
        self.received = []
        self.status = 200
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                owner.received.append(
                    (
                        self.path,
                        json.loads(raw or b"{}"),
                        self.headers.get("Authorization"),
                    )
                )
                status = 204 if self.path == "/v1/usage" else owner.status
                self.send_response(status)
                self.end_headers()
                if status != 204:
                    self.wfile.write(
                        json.dumps(
                            {"lease_id": "synthetic-lease", "reason": "spacing"}
                        ).encode()
                    )

            def do_DELETE(self):
                owner.received.append(
                    (self.path, {}, self.headers.get("Authorization"))
                )
                self.send_response(204)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self.stop)
        self.environment = patch.dict(
            os.environ,
            {
                "AA_ACCOUNT": "seat-example",
                "AA_PACE_URL": "http://127.0.0.1:" + str(self.server.server_port),
                "AA_PACE_TOKEN_FILE": str(self.token),
                "AA_LEASE_FAIL_CLOSED": "false",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def stop(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()

    def test_lease_acquire_renew_release(self):
        module = load("aa-lease")
        lease = module.available("acquire")["lease_id"]
        module.available("renew", lease)
        module.available("release", lease)
        self.assertEqual(
            [r[0] for r in self.received],
            [
                "/v1/lease",
                "/v1/lease/synthetic-lease/renew",
                "/v1/lease/synthetic-lease",
            ],
        )
        self.assertEqual(
            self.received[0][1],
            {
                "account_id": "seat-example",
                "kind": "session",
                "pod": os.environ.get("HOSTNAME", "unknown"),
            },
        )
        self.assertEqual(self.received[0][2], "Bearer synthetic-token")

    def test_hold_renews_after_sixty_seconds_and_releases(self):
        from unittest.mock import Mock
        module = load("aa-lease")
        child = Mock(returncode=0)
        child.poll.side_effect = [None, 0, 0]
        with patch.object(module.subprocess, "Popen", return_value=child) as start, patch.object(module.time, "sleep"), patch.object(module.time, "monotonic", side_effect=[0, 60, 60]):
            self.assertEqual(module.main(["hold", "--account", "chosen-account", "--", "synthetic-command"]), 0)
        start.assert_called_once_with(["synthetic-command"])
        self.assertEqual(self.received[0][1]["account_id"], "chosen-account")
        self.assertEqual([r[0] for r in self.received], ["/v1/lease", "/v1/lease/synthetic-lease/renew", "/v1/lease/synthetic-lease"])

    def test_hold_releases_when_child_cannot_start(self):
        module = load("aa-lease")
        with patch.object(module.subprocess, "Popen", side_effect=OSError), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(module.main(["hold", "--", "synthetic-command"]), 1)
        self.assertEqual([r[0] for r in self.received], ["/v1/lease", "/v1/lease/synthetic-lease"])

    def test_denial_never_fails_open(self):
        module = load("aa-lease")
        self.status = 409
        with self.assertRaisesRegex(module.Denied, "spacing"):
            module.available("acquire")
        self.status = 403
        with self.assertRaises(module.Denied):
            module.available("acquire")

    def test_unreachable_fail_open_and_fail_closed(self):
        module = load("aa-lease")
        with socket.socket() as unused:
            unused.bind(("127.0.0.1", 0))
            port = unused.getsockname()[1]
        with patch.dict(
            os.environ, {"AA_PACE_URL": "http://127.0.0.1:" + str(port)}
        ), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(module.available("acquire"), {})
            with patch.dict(os.environ, {"AA_LEASE_FAIL_CLOSED": "true"}):
                with self.assertRaises(ValueError):
                    module.available("acquire")

    def test_manual_parsing(self):
        module = load("aa-usage-report")
        values = module.manual("5h=40,weekly=22")
        self.assertEqual(
            [(v["window"], v["used_pct"]) for v in values], [("5h", 40), ("weekly", 22)]
        )
        for invalid in ("5h=101", "5h=nan", "5h=-1", "5h=1,5h=2", "bad window=2", "5h"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                module.manual(invalid)

    def test_rollout_only_numeric_rate_limits_leave_pod(self):
        module = load("aa-usage-report")
        sessions = self.root / "sessions/2026/01"
        sessions.mkdir(parents=True)
        secret_text = "synthetic transcript must remain local"
        events = [
            {"payload": {"text": secret_text}},
            {
                "payload": {
                    "rate_limits": {
                        "primary": {
                            "used_percent": 40,
                            "window_minutes": 300,
                            "resets_at": 123,
                        },
                        "secondary": {"used_percent": 22, "window_minutes": 10080},
                    },
                    "text": secret_text,
                }
            },
        ]
        (sessions / "rollout-example.jsonl").write_text(
            "\n".join(json.dumps(e) for e in events) + "\ninvalid\n"
        )
        (sessions / "other.jsonl").write_text(
            json.dumps({"rate_limits": {"primary": {"used_percent": 99}}})
        )
        readings = module.codex_readings(self.root / "sessions")
        self.assertEqual([v["used_pct"] for v in readings], [40, 22])
        for value in readings:
            module.post(value)
        encoded = json.dumps(self.received)
        self.assertNotIn(secret_text, encoded)
        self.assertEqual(
            set(self.received[0][1]),
            {"account_id", "window", "used_pct", "resets_at", "source"},
        )
        self.assertEqual(
            module.readings({"primary": {"used_percent": 2, "resets_at": secret_text}}),
            [],
        )

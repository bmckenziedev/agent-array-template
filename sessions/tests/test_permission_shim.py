"""Fail-closed stdio shim logic is portable."""
import os
import runpy
import unittest
import socket
from unittest.mock import patch
from pathlib import Path

SHIM = runpy.run_path(str(Path(__file__).resolve().parents[1] / "common/bin/aa-permission-mcp"))


class Shim(unittest.TestCase):
    def test_missing_session_denies(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(SHIM["approve"]({})["behavior"], "deny")

    def test_missing_socket_denies(self):
        def fail(*args):
            raise OSError("missing")
        with patch.dict(os.environ, {"AA_SUPERVISOR_SESSION": "synthetic"}):
            self.assertEqual(SHIM["approve"]({}, fail)["behavior"], "deny")

    def test_only_approve_tool(self):
        tools = SHIM["dispatch"]({"method": "tools/list"})["tools"]
        self.assertEqual([t["name"] for t in tools], ["approve"])

    def test_fake_socket_allow_deny_and_malformed_reply(self):
        class FakeSocket:
            def __init__(self, reply):
                self.reply = reply
                self.sent = b""
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def settimeout(self, timeout): pass
            def connect(self, path): self.path = path
            def sendall(self, data): self.sent = data
            def recv(self, maximum):
                reply, self.reply = self.reply, b""
                return reply
        for raw, expected in [(b'{"decision":"allow"}\n', "allow"),
                              (b'{"decision":"deny"}\n', "deny"), (b'broken\n', "deny")]:
            conn = FakeSocket(raw)
            with patch.dict(os.environ, {"AA_SUPERVISOR_SESSION": "synthetic"}), \
                 patch.object(socket, "AF_UNIX", getattr(socket, "AF_UNIX", 1), create=True):
                result = SHIM["approve"]({"tool_name": "read", "input": {"path": "/work/file"}}, lambda *args: conn)
            self.assertEqual(result["behavior"], expected)
            self.assertIn(b'permission.request', conn.sent)

    @unittest.skipUnless(os.name == "posix", "Unix socket transport requires POSIX")
    def test_posix_socket_available(self):
        import socket
        self.assertTrue(hasattr(socket, "AF_UNIX"))

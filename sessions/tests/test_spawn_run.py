"""Candidate CLI argv excludes the brief; pinned behavior remains VERIFY."""
import os
import runpy
import unittest
import sys
from pathlib import Path

RUNNER = runpy.run_path(str(Path(__file__).resolve().parents[1] / "common/bin/aa-spawn-run"))


class Runner(unittest.TestCase):
    def test_claude_stream_flags_and_permission_tool(self):
        args = RUNNER["command"]("claude", "default", True)
        for item in ["--input-format", "--output-format", "stream-json", "--verbose", "mcp__aa-permission__approve"]:
            self.assertIn(item, args)

    def test_codex_stdin_prompt(self):
        self.assertEqual(RUNNER["command"]("codex", "default"), ["codex", "exec", "--json", "-"])

    def test_unverified_launch_blocked(self):
        self.assertFalse(RUNNER["VERIFIED"]["claude"])
        self.assertFalse(RUNNER["VERIFIED"]["codex"])

    @unittest.skipUnless(os.name == "posix", "FIFO/O_NOFOLLOW runner requires POSIX")
    def test_no_follow_rejects_symlink(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target").write_text("synthetic")
            (root / "link").symlink_to(root / "target")
            with self.assertRaises(OSError):
                RUNNER["checked_open"](root / "link", os.O_RDONLY)

    @unittest.skipUnless(os.name == "posix", "FIFO and subprocess selector integration requires POSIX")
    def test_fake_cli_stream_input_init_output_and_child_reaped(self):
        import tempfile
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            os.mkfifo(root / "input", 0o600)
            writer = os.open(root / "input", os.O_RDWR | os.O_NONBLOCK)
            reader = RUNNER["checked_open"](root / "input", os.O_RDONLY, fifo=True)
            output = RUNNER["checked_open"](root / "events", os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            brief = "synthetic holder brief"
            os.write(writer, (json.dumps({"type": "user", "message": {"content": brief}}) + "\n").encode())
            script = (
                "import json,sys,os; message=json.loads(sys.stdin.readline()); "
                "assert message['type']=='user'; "
                "print(json.dumps({'type':'system','session_id':'synthetic-cli-id'}),flush=True)"
            )
            argv = [sys.executable, "-c", script]
            self.assertNotIn(brief, str(argv))
            try:
                self.assertEqual(RUNNER["run_child"](argv, reader, output, dict(os.environ)), 0)
            finally:
                for fd in [writer, reader, output]:
                    os.close(fd)
            self.assertIn("synthetic-cli-id", (root / "events").read_text())

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class VerificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="session-verification-")
        with patch.dict(os.environ, {"WORKSPACE": cls.tmp.name,
                                    "LITELLM_BASE": "http://127.0.0.1:1", "LITELLM_KEY": "unit-only"}):
            spec = importlib.util.spec_from_file_location("session_orchestrator",
                Path(__file__).resolve().parents[1] / "orchestrator.py")
            cls.o = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.o)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix="session-source-")
        self.o.WORK = Path(self.work.name)
        self.o.TEST_CMD = ""
        self.addCleanup(self.work.cleanup)

    def test_no_command_never_passes(self):
        self.assertIsNone(self.o.test_command())
        self.assertFalse(self.o.run_tests()[0])

    def test_absent_root_npm_package(self):
        self.o.TEST_CMD = "npm test"
        self.assertIsNone(self.o.test_command())
        self.assertFalse(self.o.run_tests()[0])

    def test_missing_npm_script_and_pass_with_no_tests(self):
        for scripts in ({}, {"test": "jest --passWithNoTests"}):
            (self.o.WORK / "package.json").write_text(json.dumps({"scripts": scripts}))
            for custom in ("", "npm test", "npx jest --passWithNoTests"):
                self.o.TEST_CMD = custom
                self.assertIsNone(self.o.test_command())
                self.assertFalse(self.o.run_tests()[0])

    def test_npm_and_python_test_plans(self):
        (self.o.WORK / "package.json").write_text('{"scripts":{"test":"jest"}}')
        cmd = self.o.test_command()
        self.assertIn("--ignore-scripts", cmd)
        self.assertTrue(cmd.endswith("npm test"))
        (self.o.WORK / "package.json").unlink()
        (self.o.WORK / "test_example.py").write_text("def test_example(): pass")
        self.assertTrue(self.o.test_command().endswith("python -m pytest -q"))

    def test_zero_exit_requires_nonzero_test_evidence(self):
        for output in ("", "install successful", "no tests found", "0 passed", "Tests: 0 total",
                       "# tests 0", "# tests 4\n# pass 0\n# skipped 4", "1 passed\nno tests found"):
            self.assertFalse(self.o.tests_executed(output), output)
        for output in ("12 passed in 0.4s", "10 passed in 1s", "Tests: 2 passed, 2 total", "# tests 4\n# pass 4",
                       "\x1b[32m1 passed\x1b[0m"):
            self.assertTrue(self.o.tests_executed(output), output)

    def test_sidecar_zero_exit_does_not_override_missing_evidence(self):
        # The real IPC path, with a call-owned simulated sidecar reply.
        with tempfile.TemporaryDirectory(prefix="session-ipc-") as tmp:
            self.o.IPC = Path(tmp) / "ipc"
            self.o.TESTBOX = Path(tmp) / "box"
            results = self.o.TESTBOX / "results"
            results.mkdir(parents=True)
            def reply(_seconds):
                req = json.loads(next(self.o.IPC.glob("req-*.json")).read_text())
                (results / f"res-{req['id']}.json").write_text(json.dumps({**req,
                    "exit": 0, "timed_out": False, "output": "no tests found"}))
            with patch.object(self.o, "test_command", return_value="npm test"), \
                 patch.object(self.o, "_tester_ready", return_value=True), \
                 patch.object(self.o.time, "sleep", side_effect=reply):
                self.assertFalse(self.o.run_tests()[0])

    def test_stream_bounds_and_close(self):
        class Stream:
            def __init__(self, chunks):
                self.chunks, self.closed = chunks, False
            def __iter__(self):
                return iter(self.chunks)
            def close(self):
                self.closed = True
        def chunk(text, finish=None):
            return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text),
                                                            finish_reason=finish)])
        stream = Stream([chunk("hello "), chunk("world", "stop")])
        self.assertEqual(self.o.join_stream(stream, self.o.time.monotonic() + 10), "hello world")
        self.assertTrue(stream.closed)
        for chunks, budget, limit in (([chunk("partial")], 10, 100),
                                      ([chunk("text", "length")], 10, 100),
                                      ([chunk("too large", "stop")], 10, 2),
                                      ([chunk("late", "stop")], -1, 100)):
            stream = Stream(chunks)
            with self.assertRaises(ValueError):
                self.o.join_stream(stream, self.o.time.monotonic() + budget, limit)
            self.assertTrue(stream.closed)

    def test_audit_omits_payload_and_credentials(self):
        with patch("builtins.print") as emitted:
            self.o.log("unit-only-secret prompt text")
        line = emitted.call_args.args[0]
        event = json.loads(line)
        self.assertNotIn("unit-only-secret", line)
        self.assertEqual(event["component"], "session-jobs")
        self.assertEqual(event["detail"], {})

    def test_model_entitlement_enforced(self):
        with self.assertRaises(ValueError):
            self.o.ask("unentitled-model", "system", "user")

    def test_model_calls_disable_client_retries(self):
        parsed = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="complete"))])
        raw = SimpleNamespace(headers={}, parse=lambda: parsed)
        with patch.object(self.o.client.chat.completions.with_raw_response,
                          "create", return_value=raw) as mocked:
            self.assertEqual(self.o.ask(self.o.M_LOCAL, "system", "user"), "complete")
            self.assertGreater(mocked.call_args.kwargs["timeout"], 0)


if __name__ == "__main__":
    unittest.main()

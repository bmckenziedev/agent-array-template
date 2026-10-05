"""Exercise the real tester on Linux, with call-owned copies outside the checkout."""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


@unittest.skipUnless(os.name == "posix", "tester requires Linux process groups and /bin/bash")
class TesterIsolationTests(unittest.TestCase):
    def test_copy_is_immutable_source_and_child_environment_has_no_task_secrets(self):
        spec = importlib.util.spec_from_file_location("session_tester",
            Path(__file__).resolve().parents[1] / "tester.py")
        tester = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tester)
        with tempfile.TemporaryDirectory(prefix="tester-isolation-") as tmp:
            root = Path(tmp)
            tester.WORK = root / "source"
            tester.WORK.mkdir()
            (tester.WORK / "source.txt").write_text("original")
            tester.TESTBOX = root / "testbox"
            tester.RESULTS = tester.TESTBOX / "results"
            tester.RESULTS.mkdir(parents=True)
            tester.RUNS = tester.TESTBOX / "runs"
            tester.HOME = tester.TESTBOX / "home"
            tester.TMP = root / "tmp"
            tester.TMP.mkdir()
            # Only synthetic values; no login or credential file is involved.
            with patch.dict(os.environ, {"TASK_TOKEN": "unit-only", "LITELLM_KEY": "unit-only"}):
                res = tester.run_one({"id": 1, "nonce": "a" * 32, "timeout": 30,
                    "cmd": "python3 -c \"import os; from pathlib import Path; "
                           "assert 'TASK_TOKEN' not in os.environ; assert 'LITELLM_KEY' not in os.environ; "
                           "Path('source.txt').write_text('changed'); print('1 passed')\""})
            self.assertEqual(res["exit"], 0, res["output"])
            self.assertEqual((tester.WORK / "source.txt").read_text(), "original")
            self.assertFalse(tester.RUNS.exists())


if __name__ == "__main__":
    unittest.main()

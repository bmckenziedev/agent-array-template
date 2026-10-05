"""Offline generator regressions; fixtures are owned temporary directories."""
import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("patch_generator", HERE.parent / "patches" / "make-patches.py")
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

class Generator(unittest.TestCase):
    def test_already_present_edits_are_noop(self):
        with tempfile.TemporaryDirectory(prefix="aa-seccomp-generator-") as tmp:
            root = pathlib.Path(tmp)
            (root / "fixture").write_bytes(b"new\n")
            with patch.object(g, "JOB", root):
                self.assertEqual(g.make("fixture", [("old\n", 1, "new\n")]), "")

    def test_offline_application_and_guarded_rules(self):
        result = subprocess.run(
            [sys.executable, str(HERE / "check_admission_patch.py"), "--offline"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("OFFLINE PASS", result.stdout)

    def test_unknown_anchor_fails(self):
        with tempfile.TemporaryDirectory(prefix="aa-seccomp-generator-") as tmp:
            root = pathlib.Path(tmp)
            (root / "fixture").write_bytes(b"unknown\n")
            with patch.object(g, "JOB", root), self.assertRaises(SystemExit):
                g.make("fixture", [("old\n", 1, "new\n")])

    def test_stale_patch_fails_check(self):
        with tempfile.TemporaryDirectory(prefix="aa-seccomp-generator-") as tmp:
            root = pathlib.Path(tmp)
            (root / "fixture.patch").write_bytes(b"stale\n")
            with patch.object(g, "HERE", root), patch.object(g, "PATCHES", {"fixture.patch": ("fixture", [])}), patch.object(g, "make", return_value="fresh\n"), patch.object(g.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
                self.assertEqual(g.main(["--check"]), 1)

if __name__ == "__main__":
    unittest.main(verbosity=2)

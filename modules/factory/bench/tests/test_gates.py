import unittest
import os
import tempfile
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from harness.gates import gate, selftest
from harness.profile import build


class GatesTest(unittest.TestCase):
    def test_planted_bad_outputs(self):
        self.assertEqual(selftest()["failed"], 0)

    def test_documentation_gate_reports_missing_node(self):
        from harness.production import doc_gate
        with patch("harness.production.shutil.which", return_value=None):
            result = doc_gate({}, Path("unused"), "candidate")
        self.assertTrue(result.startswith("skip:"), result)
        self.assertIn("Node and engine JS dependencies", result)

    def test_skipped_gates_are_not_counted_as_passed(self):
        with patch("harness.gates.gate", return_value="skip: tools unavailable"):
            result = selftest()
        self.assertEqual(result["passed"], 0)
        self.assertGreater(result["skipped"], 0)
        self.assertEqual(result["skip_reasons"], ["skip: tools unavailable"])

    @unittest.skipUnless(os.name == "posix", "POSIX fixture modes require a Unix host")
    def test_staged_fixture_is_readable_by_isolated_uid(self):
        import stat
        units = json.loads((Path(__file__).parents[1] / "examples/units.json").read_text())
        unit = next(unit for unit in units if unit["kind"] == "test_gen")
        executions = 0

        def docker(command, **kwargs):
            nonlocal executions
            if command[1] == "run":
                work = Path(command[command.index("-v") + 1].removesuffix(":/work:ro"))
                self.assertEqual(stat.S_IMODE(work.stat().st_mode), 0o755)
                for staged in work.rglob("*"):
                    expected = 0o755 if staged.is_dir() else 0o644
                    self.assertEqual(stat.S_IMODE(staged.stat().st_mode), expected)
                self.assertIn("--network=none", command)
                self.assertIn("--read-only", command)
                self.assertIn("--user=65534:65534", command)
                executions += 1
                return SimpleNamespace(returncode=0 if executions == 1 else 1)
            return SimpleNamespace(returncode=0)

        with patch("harness.gates.shutil.which", return_value="docker"), \
                patch("harness.gates.subprocess.run", side_effect=docker):
            self.assertEqual(gate(unit, unit["reference"]), "pass")
        self.assertEqual(executions, 2)

    def test_envelope_cap(self):
        self.assertEqual(gate({}, "x" * 32769), "envelope")

    def test_timed_out_candidate_container_is_removed(self):
        units = json.loads((Path(__file__).parents[1] / "examples/units.json").read_text())
        unit = next(unit for unit in units if unit["kind"] == "test_gen")
        commands = []

        def docker(command, **kwargs):
            commands.append(command)
            if command[1] == "run":
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            return SimpleNamespace(returncode=0)

        with patch("harness.gates.shutil.which", return_value="docker"), \
                patch("harness.gates.subprocess.run", side_effect=docker):
            self.assertEqual(gate(unit, unit["reference"]), "runtime")
        run = next(command for command in commands if command[1] == "run")
        name = run[run.index("--name") + 1]
        self.assertIn(["docker", "rm", "--force", name], commands)

    def test_profile_snapshot_and_traversal(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root / "small.cjs").write_text("function x() { return 1; }\nmodule.exports = {x};\n")
            definition = root / "units.json"
            definition.write_text(json.dumps([{"kind": "doc_map", "target": "small.cjs", "exports": ["x"]}]))
            unit = build(root, definition, root / "profile.json")["units"][0]
            answer = json.dumps({"x": "/** Return a constant.\n * @returns {number} The constant value.\n */"})
            result = gate(unit, answer)
            if result.startswith("skip:"):
                self.skipTest(result.removeprefix("skip: "))
            self.assertEqual(result, "pass")
            (root / "small.cjs").write_text("changed")
            self.assertEqual(gate(unit, answer), "snapshot")
            unit["target"] = "../outside"
            self.assertEqual(gate(unit, answer), "snapshot")

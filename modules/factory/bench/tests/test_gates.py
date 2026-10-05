import unittest
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
            self.assertEqual(gate(unit, answer), "pass")
            (root / "small.cjs").write_text("changed")
            self.assertEqual(gate(unit, answer), "snapshot")
            unit["target"] = "../outside"
            self.assertEqual(gate(unit, answer), "snapshot")

"""Production inventory, packer and doc gate over a newly written synthetic repo."""
import asyncio
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from factory_engine import config, jsbridge
from factory_engine.gates import GateRefused, LocalGateRunner
from factory_engine.packer import PackError, Repo, pack
from factory_engine.schema import validate_or_raise
from factory_engine.tokens import Counter

FIXTURE = Path(__file__).parent / "fixtures/synthetic-mini"
VALID_DOC = "/**\n * Clamp a score between zero and a supplied ceiling.\n * @param {number} value Score to normalize.\n * @param {number} ceiling Highest allowed score.\n * @returns {number} Normalized score.\n */"


def tools_ready():
    try:
        jsbridge.node_bin()
        jsbridge.check_tools()
        return True
    except jsbridge.JsToolError:
        return False


@unittest.skipUnless(tools_ready(), "Node and pinned engine JS tools unavailable; install with npm ci --ignore-scripts")
class SyntheticProductionGateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo_path = self.root / "repo"
        shutil.copytree(FIXTURE, self.repo_path)
        self.profile = json.loads((FIXTURE / "profile.json").read_text())
        validate_or_raise("profile.v1", self.profile)
        self.repo = Repo.load("synthetic-score", self.repo_path, roots=["src"])
        self.card = {"card": 1, "task_id": "1" * 16, "unit_id": "doc-synthetic-score",
                     "repo": "synthetic-score", "kind": "doc_map", "difficulty": "easy",
                     "target": {"file": "src/score.js", "symbols": ["clampScore"]},
                     "instruction": "Describe the score clamp and all parameters.",
                     "output": "doc_map_json", "verify": "docs_tsc"}
        self.card, self.packed = pack(self.card, self.repo, self.profile, None,
                                      Counter(), config.DEFAULT_LANES)

    def tearDown(self):
        self.temp.cleanup()

    def gate(self, code, card=None):
        async def run():
            runner = LocalGateRunner(parallel=1, scratch=self.root / "gate-scratch")
            await runner.start({"synthetic-score": self.repo_path})
            try:
                return await runner.run("synthetic-score", {"kind": "doc_map",
                    "unit": card or self.card, "profile": self.profile,
                    "code": code, "options": {"returnSpliced": True}}, timeout=60)
            finally:
                await runner.close()
        return asyncio.run(run())

    def test_packed_prompt_valid_doc_and_original_snapshot_unchanged(self):
        original = (self.repo_path / "src/score.js").read_bytes()
        self.assertEqual(len(self.card["target"]["slice_sha256"]), 64)
        self.assertIn("clampScore", self.packed.user)
        self.assertTrue(self.packed.lane_fit)
        result = self.gate(json.dumps({"clampScore": VALID_DOC}))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["stage"], "pass")
        self.assertEqual(result["checks"]["tsc"]["new"], 0)
        self.assertIn(VALID_DOC, result["spliced"])
        self.assertEqual((self.repo_path / "src/score.js").read_bytes(), original)

    def test_gate_rejects_invalid_json_unknown_symbol_any_and_hygiene(self):
        cases = [("malformed JSON", "parse"),
                 (json.dumps({"clampScore": VALID_DOC, "inventedSymbol": VALID_DOC}), "scope"),
                 (json.dumps({"clampScore": VALID_DOC.replace("{number}", "{any}")}), "type_strength"),
                 (json.dumps({"clampScore": VALID_DOC.replace(" * @returns", " * @ts-ignore\n * @returns")}), "hygiene"),
                 (json.dumps({"clampScore": VALID_DOC.replace(" * @param {number} ceiling Highest allowed score.\n", "")}), "doc_coverage")]
        for code, stage in cases:
            with self.subTest(stage=stage):
                result = self.gate(code)
                self.assertFalse(result["ok"], result)
                self.assertEqual(result["stage"], stage, result)

    def test_incorrect_concrete_types_create_tsc_errors(self):
        result = self.gate(json.dumps({"clampScore": VALID_DOC.replace("{number} value", "{string} value")}))
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["stage"], "tsc", result)
        self.assertGreater(result["tsc"]["new"], 0)

    def test_target_hash_blocks_changed_snapshot(self):
        card = copy.deepcopy(self.card)
        card["target"]["slice_sha256"] = "0" * 64
        result = self.gate(json.dumps({"clampScore": VALID_DOC}), card)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["stage"], "target")

    def test_packer_refuses_invented_target(self):
        card = copy.deepcopy(self.card)
        card["target"]["symbols"] = ["inventedSymbol"]
        with self.assertRaises(PackError) as raised:
            pack(card, self.repo, self.profile, None, Counter(), config.DEFAULT_LANES)
        self.assertEqual(raised.exception.code, "TARGET_MISSING")


class LocalIsolationPolicyTest(unittest.TestCase):
    def test_local_runner_refuses_generated_test_execution(self):
        with self.assertRaises(GateRefused):
            LocalGateRunner().check_kind("test_gen")


if __name__ == "__main__":
    unittest.main()

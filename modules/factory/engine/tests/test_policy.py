"""Fixture-free security gate policy regressions."""
import asyncio
import json
from pathlib import Path
import unittest
from factory_engine.cards import CardError, validate_card
from factory_engine.gates import GateRefused, LocalGateRunner
from factory_engine.lanes import LaneScheduler, NoLane
from factory_engine import config, schema


def card():
    return {"card": 1, "unit_id": "label-doc", "task_id": "0123456789abcdef", "repo": "toy-repo",
            "kind": "doc_map", "difficulty": "easy", "target": {"file": "src/labels.js", "symbols": ["formatLabel"]},
            "instruction": "Document parameters and return value.", "output": "doc_map_json", "verify": "docs_tsc"}


class PolicyTest(unittest.TestCase):
    def test_local_runner_cannot_execute_generated_tests(self):
        runner = LocalGateRunner()
        with self.assertRaises(GateRefused):
            asyncio.run(runner.run("toy", {"kind": "test_gen", "code": "untrusted"}))

    def test_protected_targets_rejected_before_packing(self):
        for name in ("package.json", ".github/workflows/build.js", "src/value.test.js", "node_modules/dep.js", ".factory/cache.js"):
            value = card()
            value["target"]["file"] = name
            with self.assertRaises(CardError): validate_card(value, set())

    def test_test_gen_flag_off(self):
        value = card()
        value.update(kind="test_gen", output="test_file", verify="tests", provides={"test_file": "src/labels.test.js"})
        with self.assertRaises(CardError) as caught:
            validate_card(value, set())
        self.assertIn(caught.exception.code, ("FLAG_OFF", "SCHEMA"))

    def test_new_examples_schema_valid(self):
        examples = Path(__file__).resolve().parents[1] / "examples"
        schema.validate_or_raise("lanes.v1", json.loads((examples / "lanes.cluster.json").read_text()))
        schema.validate_or_raise("template.v1", json.loads((examples / "template.example.json").read_text()))

    def test_shared_lane_enforces_restricted_policy(self):
        lanes = config.load_lanes(None)
        for lane in lanes["lanes"].values(): lane["local"] = False
        sched = LaneScheduler(lanes, teams={"alpha": {"factory": {"max_inflight_per_lane": 1}}})
        async def request():
            async with sched.slot({"lane": "lane-gpu-a"}, 100, 100, team_id="alpha", data_class="restricted"):
                self.fail("restricted work entered remote lane")
        with self.assertRaises(NoLane): asyncio.run(request())

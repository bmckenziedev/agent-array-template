"""Retries reenter team dispatch instead of crossing lanes inside one lease."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from .helpers import TempDir
from factory_engine import config
from factory_engine.engine import Engine
from factory_engine.lanes import LaneScheduler
from factory_engine.store import Store
from factory_engine.tokens import Counter


class GenerationTest(unittest.TestCase):
    def build(self, root, cancelled=False):
        store = Store(root / "factory.db")
        store.create_task("0123456789abcdef", None, "/snapshot", {})
        card = {"unit_id": "toy-doc", "task_id": "0123456789abcdef", "repo": "toy", "kind": "doc_map",
                "difficulty": "easy", "target": {"file": "src/toy.js", "symbols": ["label"]},
                "budget": {"max_tokens": 100}, "retry": {"max_generations": 3}}
        store.add_unit(card, 1)
        cfg = config.load_lanes(None)
        cfg["routing"]["doc_map"]["easy"] = [{"lane": "lane-gpu-a"}, {"lane": "lane-gpu-b", "feedback": True}]
        engine = Engine.__new__(Engine)
        engine.store = store
        engine.profiles = {}
        engine.counter = Counter()
        engine.sched = LaneScheduler(cfg)
        engine.stop = lambda: cancelled
        engine.log = lambda message: None
        engine.render = lambda *args: ("system", "untrusted data", 10)
        engine._generate = AsyncMock(return_value=SimpleNamespace(
            latency_s=.01, envelope=SimpleNamespace(ok=False, reason="truncated", code=""),
            prompt_tokens=10, completion_tokens=1, stop="length", text="bad"))
        return store, engine

    def test_one_generation_requeues_and_pins_next_lane(self):
        with TempDir() as root:
            store, engine = self.build(root)
            store.claim("toy-doc")
            asyncio.run(engine.process(store.unit("toy-doc"), one_generation=True))
            unit = store.unit("toy-doc")
            self.assertEqual(unit["status"], "queued")
            self.assertEqual(unit["generations"], 1)
            self.assertEqual(store.events()[-1]["detail"]["next_lane"], "lane-gpu-b")
            self.assertEqual(len(store.attempts("toy-doc")), 1)
            store.close()

    def test_cancellation_after_generation_skips_verifier(self):
        with TempDir() as root:
            store, engine = self.build(root, cancelled=True)
            engine.runner = SimpleNamespace(run=AsyncMock())
            store.claim("toy-doc")
            asyncio.run(engine.process(store.unit("toy-doc"), one_generation=True))
            self.assertEqual(store.unit("toy-doc")["status"], "cancelled")
            self.assertEqual(store.attempts("toy-doc")[0]["stage"], "cancelled")
            engine.runner.run.assert_not_called()
            store.close()

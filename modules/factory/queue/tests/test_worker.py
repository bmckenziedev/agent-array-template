"""Actual foreground cross-batch dispatch, with a bounded fake model/gate."""
import asyncio
from collections import Counter
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from factory_engine.store import Store
from factory_queue.api_store import BatchStore
from factory_queue import worker


class Runner:
    async def start(self, repos):
        pass
    async def close(self):
        pass


class ForegroundTest(unittest.TestCase):
    def test_global_units_use_weighted_lane_dispatch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            teams = [{"id": "alpha", "weight": 3, "leads": ["ana"], "factory": {"max_inflight_per_lane": 1}},
                     {"id": "beta", "weight": 2, "leads": ["ben"], "factory": {"max_inflight_per_lane": 1}}]
            estates = [{"id": "estate-a", "owner_team": "alpha", "data_class": "internal"},
                       {"id": "estate-b", "owner_team": "beta", "data_class": "internal"}]
            queue = BatchStore(root / "queue.db", estates, teams)
            template = {"template": 1, "template_id": "toy", "kind": "doc_map", "expand": {
                "via": "find_undocumented", "repo": "toy", "roots": ["src"]},
                "card": {"difficulty": "easy"}, "profile": {"profile": 1}}
            for team, user, estate in [("alpha", "ana", "estate-a"), ("beta", "ben", "estate-b")]:
                identity = {"user": user, "teams": [team], "primary_team": team}
                for _ in range(7):
                    batch = queue.submit({"estate_id": estate, "template": template}, identity)
                    queue.approve(batch["batch_id"], identity)
            stopped = threading.Event()
            seen = []

            def prepare(store, batch, home, lanes, snapshot):
                task = Store(Path(":memory:"))
                task.create_task(batch["batch_id"], None, "/snapshot", {}, team_id=batch["team_id"])
                for index in range(100):
                    task.add_unit({"unit_id": str(index), "task_id": batch["batch_id"], "repo": "toy",
                                   "kind": "doc_map", "difficulty": "easy", "target": {
                                       "file": "src/toy.js", "symbols": ["combine"]}}, index)
                return task

            class Engine:
                def __init__(self, path, task, stop, log=None):
                    self.store = task
                    self.repos = {}
                    self.runner = Runner()
                    self.profiles = {}
                async def _baselines(self):
                    pass
                def _check_pause(self):
                    pass
                async def process(self, unit, *, one_generation=False):
                    seen.append(unit["team_id"])
                    self.store.set_unit(unit["unit_id"], status="accepted")
                    if len(seen) == 1000:
                        stopped.set()

            lanes = root / "lanes.json"
            lanes.write_text(json.dumps({"lanes": {"lane-gpu-a": {"concurrency": 1},
                                                    "lane-gpu-b": {"concurrency": 1}},
                                          "routing": {"doc_map": {"easy": [{"lane": "lane-gpu-a"}]}}}))
            async def assemble(*args, **kwargs):
                return {}
            with patch.object(worker, "prepare", prepare), patch.object(worker, "Engine", Engine), \
                    patch.object(worker.bundle, "assemble", assemble), \
                    patch.object(worker.bundle, "build", return_value={"verified": True, "bundle_tgz": "bundle.tgz", "verdict": "pass"}):
                asyncio.run(asyncio.wait_for(worker.run_foreground(queue, stopped, root, lanes, poll_seconds=.001), timeout=60))
            counts = Counter(seen)
            self.assertEqual(len(seen), 1000)
            self.assertLessEqual(abs(counts["alpha"] / 1000 - .6), .05)
            self.assertLessEqual(abs(counts["beta"] / 1000 - .4), .05)
            queue.close()

    def test_lead_approval_freezes_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            estate = root / "estates" / "toy"
            estate.mkdir(parents=True)
            file = estate / "toy.js"
            file.write_text("exports.combine = (a, b) => a + b;\n")
            queue = BatchStore(root / "state" / "queue.db", [{"id": "toy", "owner_team": "alpha", "data_class": "restricted"}],
                               [{"id": "alpha", "leads": ["ana"]}], snapshot_root=root / "estates")
            identity = {"user": "ana", "teams": ["alpha"], "primary_team": "alpha"}
            template = {"template": 1, "template_id": "toy", "kind": "doc_map", "expand": {
                "via": "find_undocumented", "repo": "toy", "roots": ["src"]}, "card": {"difficulty": "easy"}}
            batch = queue.submit({"estate_id": "toy", "template": template}, identity)
            approved = queue.approve(batch["batch_id"], identity)
            file.write_text("unapproved replacement")
            frozen = Path(approved["payload"]["_snapshot_dir"]) / "toy.js"
            self.assertIn("exports.combine", frozen.read_text())
            queue.close()

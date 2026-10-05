"""Team admission and old database migration proofs."""
import sqlite3
import unittest
from .helpers import TempDir
from factory_engine.scheduler import TeamScheduler
from factory_engine.store import DDL, Store


def teams(a=3, b=2, cap=2):
    return [{"id": "alpha", "weight": a, "factory": {"max_inflight_per_lane": cap, "queue_priority_ceiling": 5}},
            {"id": "beta", "weight": b, "factory": {"max_inflight_per_lane": cap, "queue_priority_ceiling": 3}}]


def units():
    return [{"unit_id": "a", "team_id": "alpha", "priority": 0, "seq": 1},
            {"unit_id": "b", "team_id": "beta", "priority": 0, "seq": 2}]


class FairnessTest(unittest.TestCase):
    def test_weights_converge_over_1000_dispatches(self):
        sched = TeamScheduler(teams(), {"a": {"local": True}})
        counts = {"alpha": 0, "beta": 0}
        for _ in range(1000):
            unit = sched.choose(units(), "a")
            counts[unit["team_id"]] += 1
            sched.release("a", unit["team_id"])
        self.assertLessEqual(abs(counts["alpha"] / 1000 - .6), .05)
        self.assertLessEqual(abs(counts["beta"] / 1000 - .4), .05)

    def test_starvation_guard(self):
        sched = TeamScheduler(teams(1000, 1), {"a": {}}, starvation_n=8)
        last = {"alpha": -1, "beta": -1}
        for dispatch in range(1000):
            unit = sched.choose(units(), "a")
            team = unit["team_id"]
            self.assertLessEqual(dispatch - last[team], 8)
            last[team] = dispatch
            sched.release("a", team)
        self.assertLessEqual(999 - last["beta"], 8)

    def test_per_team_cap_and_lane_independence(self):
        sched = TeamScheduler(teams(cap=1), {"a": {"local": True}, "b": {"local": True}})
        work = [units()[0]]
        self.assertIsNotNone(sched.choose(work, "a"))
        self.assertIsNone(sched.choose(work, "a"))
        self.assertIsNotNone(sched.choose(work, "b"))
        sched.release("a", "alpha")
        self.assertIsNotNone(sched.choose(work, "a"))

    def test_restricted_lane_gate(self):
        sched = TeamScheduler(teams(), {"remote": {"local": False}, "local": {"local": True}})
        work = [{**units()[0], "data_class": "restricted"}]
        self.assertIsNone(sched.choose(work, "remote"))
        self.assertIsNotNone(sched.choose(work, "local"))

    def test_priority_ceiling_then_sequence(self):
        sched = TeamScheduler(teams(), {"a": {}})
        work = [{**units()[0], "priority": 99, "seq": 9},
                {**units()[0], "priority": 5, "seq": 1}]
        self.assertEqual(sched.choose(work, "a")["seq"], 1)

    def test_migration_preserves_old_task(self):
        with TempDir() as root:
            db = sqlite3.connect(root / "old.db")
            db.executescript(DDL)
            db.execute("INSERT INTO meta VALUES('schema','1')")
            db.execute("INSERT INTO task(task_id,status,config) VALUES('legacy','open','{}')")
            db.execute("INSERT INTO unit(unit_id,task_id,status,card,symbols,deps) VALUES('old-unit','legacy','accepted','{}','[]','[]')")
            db.execute("INSERT INTO event(type,detail) VALUES('legacy_event','{}')")
            db.commit()
            db.close()
            store = Store(root / "old.db")
            self.assertEqual(store.task()["task_id"], "legacy")
            self.assertEqual(store.unit("old-unit")["status"], "accepted")
            self.assertIsNone(store.task()["team_id"])
            self.assertEqual(store.events()[0]["type"], "legacy_event")
            store.db.execute("DELETE FROM task")
            store.create_task("task", None, "/snapshot", {}, team_id="alpha", submitted_by="ana", data_class="restricted", estate_id="toy")
            self.assertEqual(store.task()["team_id"], "alpha")
            store.event("approval", actor="subject", actor_kind="lead")
            self.assertEqual(store.events()[-1]["actor_kind"], "lead")
            store.close()
            reopened = Store(root / "old.db")
            self.assertEqual(reopened.task()["submitted_by"], "ana")
            reopened.close()

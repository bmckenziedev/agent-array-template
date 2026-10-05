"""Lane scheduler (semaphores, stealing, fit) and the SQLite store."""
import asyncio
import os
import time
import unittest

from .helpers import TempDir

from factory_engine import config
from factory_engine.lanes import LaneScheduler, NoLane
from factory_engine.store import Store


def cfg(c14=1, c7=3, steal=True):
    c = config.load_lanes(None)
    c["lanes"]["lane-gpu-a"]["concurrency"] = c14
    c["lanes"]["lane-gpu-b"]["concurrency"] = c7
    c["steal"] = steal
    return c


class LaneTest(unittest.TestCase):
    def run_jobs(self, sched, rung, n, hold=0.05):
        seen = {"lane-gpu-a": 0, "lane-gpu-b": 0}
        peak = {"lane-gpu-a": 0, "lane-gpu-b": 0}
        stolen = []

        async def job():
            async with sched.slot(rung, 1000, 400) as (lane, st):
                seen[lane.name] += 1
                peak[lane.name] = max(peak[lane.name], lane.inflight)
                stolen.append(st)
                await asyncio.sleep(hold)

        async def main():
            await asyncio.gather(*(job() for _ in range(n)))
        asyncio.run(main())
        return seen, peak, stolen

    def test_semaphores_cap_concurrency(self):
        sched = LaneScheduler(cfg(steal=False))
        seen, peak, _ = self.run_jobs(sched, {"lane": "lane-gpu-b"}, 12)
        self.assertEqual(peak["lane-gpu-b"], 3)
        self.assertEqual(seen["lane-gpu-a"], 0)
        seen, peak, _ = self.run_jobs(LaneScheduler(cfg(steal=False)), {"lane": "lane-gpu-a"}, 5)
        self.assertEqual(peak["lane-gpu-a"], 1)

    def test_steal_when_saturated(self):
        sched = LaneScheduler(cfg())
        seen, peak, stolen = self.run_jobs(sched, {"lane": "lane-gpu-b", "steal_to": ["lane-gpu-a"]}, 10)
        self.assertGreater(seen["lane-gpu-a"], 0)
        self.assertLessEqual(peak["lane-gpu-a"], 1)
        self.assertLessEqual(peak["lane-gpu-b"], 3)
        self.assertEqual(sum(stolen), seen["lane-gpu-a"])

    def test_fit_and_noland(self):
        c = cfg()
        c["lanes"]["lane-gpu-a"]["prompt_cap"] = 4600
        c["lanes"]["lane-gpu-b"]["prompt_cap"] = 6900
        sched = LaneScheduler(c)
        big = 6000          # artificial caps for scheduler isolation
        lanes = sched.candidates({"lane": "lane-gpu-a", "steal_to": ["lane-gpu-b"]}, big, 400)
        self.assertEqual([l.name for l in lanes], ["lane-gpu-b"])

        async def nolane():
            async with sched.slot({"lane": "lane-gpu-a"}, big, 400):
                pass
        with self.assertRaises(NoLane):
            asyncio.run(nolane())

    def test_ladder_is_at_most_three(self):
        sched = LaneScheduler(cfg())
        self.assertEqual(len(sched.ladder("doc_map", "easy", 9)), 3)
        self.assertEqual(len(sched.ladder("doc_map", "easy", 1)), 1)
        self.assertEqual(sched.ladder("doc_map", "normal", 3)[0]["lane"], "lane-gpu-a")

    def test_bad_routing_rejected(self):
        c = cfg()
        c["routing"]["doc_map"]["easy"] = c["routing"]["doc_map"]["easy"] * 2
        with self.assertRaises(ValueError):
            config.check_lanes(c)


class StoreTest(unittest.TestCase):
    def card(self, uid):
        return {"card": 1, "unit_id": uid, "task_id": "0123456789abcdef", "repo": "toy-badges", "kind": "doc_map",
                "difficulty": "easy", "target": {"file": "src/badges.js", "symbols": ["labelBadge"]}, "priority": 0,
                "provenance": {"template_id": "t"}}

    def test_lifecycle_counts_and_lease(self):
        with TempDir() as d:
            s = Store(d / "factory.db")
            s.create_task("0123456789abcdef", None, str(d), {"x": 1})
            for i in range(3):
                s.add_unit(self.card(f"u{i}"), i + 1)
            self.assertEqual(s.counts()["queued"], 3)
            self.assertTrue(s.claim("u0"))
            self.assertFalse(s.claim("u0"))
            s.set_unit("u0", status="accepted", output="{}", gate={"ok": True})
            self.assertEqual(s.unit("u0")["gate"], {"ok": True})
            self.assertTrue(s.claim("u1"))
            self.assertEqual(s.requeue_running(), 1)
            aid = s.add_attempt("u0", gen=1, lane="l", stage=None)
            s.update_attempt(aid, stage="pass", ok=1)
            self.assertEqual(s.attempts("u0")[0]["stage"], "pass")
            s.set_unit("u2", not_before=time.time() + 60)
            self.assertEqual([u["unit_id"] for u in s.queued(10)], ["u1"])
            self.assertEqual(s.cancel_queued(), 2)
            self.assertTrue(s.take_lease(30))
            self.assertTrue(s.worker_alive(30))
            s2 = Store(d / "factory.db")
            s2.db.execute("UPDATE worker SET pid=pid+1")    # pretend another process holds a fresh lease
            self.assertFalse(s2.take_lease(30))
            s2.db.execute("UPDATE worker SET heartbeat=0")  # stale -> may be taken over
            self.assertTrue(s2.take_lease(30))
            s.close()
            s2.close()

    def test_no_prompts_or_keys_columns(self):
        with TempDir() as d:
            s = Store(d / "f.db")
            cols = {r[1] for t in ("unit", "attempt", "task") for r in s.db.execute(f"PRAGMA table_info({t})")}
            self.assertFalse({"prompt", "key", "api_key"} & cols)
            s.close()


if __name__ == "__main__":
    unittest.main()

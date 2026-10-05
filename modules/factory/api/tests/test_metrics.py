"""Metrics coherence, replay and concurrent occupancy contract."""

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from factory_api.metrics import Metrics, union_seconds


class FakeQueue:
    def __init__(self):
        import threading
        self.lock = threading.RLock()
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE batches(batch_id TEXT,status TEXT)")


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.queue = FakeQueue()
        self.path = self.home / "tasks" / "toy" / "factory.db"
        self.path.parent.mkdir(parents=True)
        self.db = sqlite3.connect(self.path)
        self.db.executescript("""
            CREATE TABLE task(status TEXT,config TEXT);
            CREATE TABLE unit(unit_id TEXT,kind TEXT,difficulty TEXT,status TEXT,lane TEXT,
                              deps TEXT,not_before REAL);
            CREATE TABLE attempt(id INTEGER,unit_id TEXT,stage TEXT,ok INTEGER,lane TEXT);
            CREATE TABLE event(id INTEGER,unit_id TEXT,type TEXT,detail TEXT);
        """)
        cfg = {"lanes": {"routing": {"doc_map": {"easy": [{"lane": "lane-gpu-a"}]}}}}
        self.db.execute("INSERT INTO task VALUES('running',?)", (json.dumps(cfg),))
        self.db.execute("INSERT INTO unit VALUES('u1','doc_map','easy','running',NULL,'[]',0)")
        self.db.execute("INSERT INTO unit VALUES('u2','doc_map','easy','queued',NULL,'[]',0)")
        self.db.execute("INSERT INTO unit VALUES('u3','doc_map','easy','queued',NULL,'[\"u1\"]',0)")
        self.db.execute("INSERT INTO attempt VALUES(1,'u1','pass',1,'lane-gpu-a')")
        self.db.execute("INSERT INTO unit VALUES('u4','doc_map','easy','bounced','lane-gpu-a','[]',0)")
        self.db.execute("INSERT INTO event VALUES(1,'u4','unit_bounced',?)",
                        (json.dumps({"lane": "lane-gpu-a"}),))
        for event_id, start, end in ((2, 10, 20), (3, 15, 25)):
            self.db.execute("INSERT INTO event VALUES(?,NULL,'generation_occupancy',?)",
                            (event_id, json.dumps({"lane": "lane-gpu-a", "start": start, "end": end})))
        self.db.commit()
        self.metrics = Metrics(self.home, {"lane-gpu-a": {"node": "gpu-a"}}, self.queue)

    def tearDown(self):
        self.metrics.close()
        self.db.close()
        self.queue.db.close()
        self.temp.cleanup()

    def test_union_excludes_double_count(self):
        self.assertEqual(union_seconds([(10, 20), (15, 25), (30, 32)]), 17)

    def test_coherent_counts_and_unknown_power(self):
        text = self.metrics.refresh()
        self.assertIn('aa_factory_queue_depth{lane="lane-gpu-a",node="gpu-a"} 3', text)
        self.assertIn('aa_factory_ready{lane="lane-gpu-a",node="gpu-a"} 1', text)
        self.assertIn('aa_factory_inflight{lane="lane-gpu-a",node="gpu-a"} 1', text)
        self.assertIn('aa_factory_gpu_seconds_total{lane="lane-gpu-a",node="gpu-a"} 15.0', text)
        self.assertNotIn("node_suspended", text)
        self.assertNotIn("u1", text)

    def test_replay_restart_retention_and_freshness(self):
        self.db.execute("UPDATE task SET status='finished'")
        self.db.commit()
        first = self.metrics.refresh()
        self.metrics.refresh()
        count = self.metrics.ledger.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        self.assertEqual(count, 2)
        before = self.metrics.snapshot
        self.db.execute("DROP TABLE unit")
        self.db.commit()
        self.assertEqual(self.metrics.refresh(), self.metrics.last_text)
        self.assertEqual(self.metrics.snapshot, before)
        self.db.close()
        self.path.unlink()
        self.db = sqlite3.connect(self.path)
        self.db.close()
        self.path.unlink()
        self.metrics.close()
        self.metrics = Metrics(self.home, {"lane-gpu-a": {"node": "gpu-a"}}, self.queue)
        after = self.metrics.refresh()
        self.assertIn('kind="doc_map",outcome="pass"} 1', first)
        self.assertIn('kind="doc_map",outcome="pass"} 1', after)
        self.assertIn('aa_factory_gpu_seconds_total{lane="lane-gpu-a",node="gpu-a"} 15.0', after)
        self.db = sqlite3.connect(":memory:")


if __name__ == "__main__":
    unittest.main()

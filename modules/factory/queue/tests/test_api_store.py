"""Team approval, durability, bounds and cancellation checks."""
import tempfile
from pathlib import Path
import unittest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "engine"))
from factory_queue.api_store import BatchStore


class QueueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "queue.db"
        self.teams = [{"id": "alpha", "leads": ["ana"], "factory": {"queue_priority_ceiling": 5}},
                      {"id": "beta", "leads": ["ben"]}]
        self.estates = [{"id": "toy", "owner_team": "alpha", "data_class": "restricted"}]
        self.queue = BatchStore(self.path, self.estates, self.teams)
        self.member = {"user": "mira", "sub": "subject-m", "teams": ["alpha"], "primary_team": "alpha"}
        self.lead = {"user": "ana", "sub": "subject-a", "teams": ["alpha"], "primary_team": "alpha"}
        self.other = {"user": "ben", "teams": ["beta"], "primary_team": "beta"}
        self.payload = {"estate_id": "toy", "template": {"template": 1, "template_id": "toy-docs", "kind": "doc_map", "expand": {"via": "find_undocumented", "repo": "toy", "roots": ["src"]}, "card": {"difficulty": "easy"}, "profile": {"profile": 1}}, "cards": [], "priority": 99}

    def tearDown(self):
        self.queue.close()
        self.tmp.cleanup()

    def test_lead_only_approval_and_durable_actor(self):
        batch = self.queue.submit(self.payload, self.member)
        bid = batch["batch_id"]
        self.assertEqual(batch["status"], "pending-approval")
        self.assertEqual(batch["priority"], 5)
        with self.assertRaises(PermissionError): self.queue.approve(bid, self.member)
        with self.assertRaises(PermissionError): self.queue.approve(bid, self.other)
        self.queue.approve(bid, self.lead)
        self.assertEqual(self.queue.events(bid)[1]["actor"], "subject-a")
        self.assertEqual(self.queue.events(bid)[1]["actor_kind"], "lead")
        self.queue.close()
        self.queue = BatchStore(self.path, self.estates, self.teams)
        self.assertEqual(self.queue.get(bid)["status"], "ready")

    def test_estate_allowlist_and_bounds(self):
        with self.assertRaises(PermissionError): self.queue.submit(self.payload, self.other)
        with self.assertRaises(ValueError): self.queue.submit({**self.payload, "cards": [{}] * 101}, self.member)
        self.queue.caps["max_batches"] = 1
        self.queue.submit(self.payload, self.member)
        with self.assertRaises(ValueError): self.queue.submit(self.payload, self.member)

    def test_cancel_and_fenced_completion(self):
        batch = self.queue.submit(self.payload, self.member)
        bid = batch["batch_id"]
        self.queue.approve(bid, self.lead)
        claim = self.queue.claim(bid)
        self.assertIsNone(self.queue.claim(bid))
        with self.assertRaises(PermissionError): self.queue.cancel(bid, self.other)
        self.queue.cancel(bid, self.member)
        self.assertFalse(self.queue.complete(bid, claim["claim_id"], {}))
        self.assertEqual(self.queue.get(bid)["status"], "cancelled")

    def test_crash_lease_recovery(self):
        bid = self.queue.submit(self.payload, self.member)["batch_id"]
        self.queue.approve(bid, self.lead)
        old = self.queue.claim(bid)
        self.queue.db.execute("UPDATE batches SET lease_until=0 WHERE batch_id=?", (bid,))
        self.queue.recover()
        fresh = self.queue.claim(bid)
        self.assertFalse(self.queue.complete(bid, old["claim_id"], {}))
        self.assertTrue(self.queue.complete(bid, fresh["claim_id"], {"accepted": 1}))

    def test_prune_uses_finished_time_and_retains_audit(self):
        bid = self.queue.submit(self.payload, self.member)["batch_id"]
        self.queue.db.execute("UPDATE batches SET created=0 WHERE batch_id=?", (bid,))
        self.queue.cancel(bid, self.member)
        self.assertEqual(self.queue.prune(1), 0)
        self.queue.db.execute("UPDATE batches SET finished=0 WHERE batch_id=?", (bid,))
        self.assertEqual(self.queue.prune(1), 1)
        self.assertEqual(self.queue.events(bid)[-1]["event"], "batch.cancel")

    def test_reserved_service_fields_and_unsafe_profile_paths(self):
        with self.assertRaises(ValueError):
            self.queue.submit({**self.payload, "_snapshot_dir": "/host"}, self.member)
        value = {**self.payload, "template": {**self.payload["template"], "profile": {"profile": 1, "sources": ["/host"]}}}
        with self.assertRaises(ValueError):
            self.queue.submit(value, self.member)

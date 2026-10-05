"""Ported bounded snapshot-queue and workstation-result regression cases.

The widget snapshot below is freshly written synthetic code, not an estate copy.
"""
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from factory_queue.snapshot_queue import Queue, digest, safe_path, verify_stored
from factory_engine.store import Store
import remote
import service


def synthetic(root):
    snapshot = root / "snapshot"
    files = {"widgets/package.json": '{"name":"@example-org/widgets","version":"1.0.0"}\n',
             "widgets/src/join.js": 'function join(left, right) { return `${left}:${right}`; }\nmodule.exports = { join };\n'}
    hashes = {}
    for name, text in files.items():
        path = snapshot / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    lane = {"tier": "toy", "concurrency": 1, "ctx": 8192, "prompt_cap": 4096,
            "endpoint": {"base_url": "http://127.0.0.1:18080", "api": "openai-completions",
                         "model": "synthetic-local", "template": "chatml", "timeout_s": 5}}
    template = {"template": 1, "template_id": "widget-docs", "kind": "doc_map",
                "expand": {"via": "find_undocumented", "repo": "widgets", "roots": ["src"],
                           "filter": {"symbols": ["join"]}, "cap": 1},
                "card": {"difficulty": "easy"}, "profile": {"profile": 1, "sources": ["src"]}}
    return {"version": 1, "snapshot": {"root": str(snapshot), "files": hashes, "digest": digest(hashes)},
            "inputs": [{"type": "template", "value": template}],
            "lanes": {"lanes": {"lane-gpu-a": copy.deepcopy(lane), "lane-gpu-b": copy.deepcopy(lane)},
                      "gate_parallel": 1, "max_inflight_units": 1, "infra_retries": 0},
            "limits": {"units": 1, "seconds": 5, "retention_seconds": 3600}}


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.spec = synthetic(self.root)
        self.queue = Queue(self.root / "queue")

    def tearDown(self):
        self.queue.db.close()
        self.tmp.cleanup()

    def enqueue(self):
        return self.queue.enqueue(self.spec, actor="subject-lead", team_id="platform")["id"]

    def test_explicit_lead_event_dedupe_and_unchanged_source(self):
        with self.assertRaises(PermissionError):
            self.queue.enqueue(self.spec)
        before = (self.root / "snapshot/widgets/src/join.js").read_bytes()
        bid = self.enqueue()
        self.assertTrue(self.queue.enqueue(self.spec, actor="subject-lead", team_id="platform")["deduplicated"])
        event = json.loads(self.queue.db.execute("SELECT detail FROM ledger WHERE event='approved'").fetchone()[0])
        self.assertEqual(event["actor_kind"], "lead")
        self.assertEqual(event["actor"], "subject-lead")
        self.assertEqual(before, (self.root / "snapshot/widgets/src/join.js").read_bytes())
        verify_stored(self.queue.home / bid, self.spec)

    def test_changed_source_refused(self):
        (self.root / "snapshot/widgets/src/join.js").write_text("changed")
        with self.assertRaises(ValueError): self.enqueue()

    def test_changed_or_unlisted_stored_file_refused(self):
        bid = self.enqueue()
        root = self.queue.home / bid / "snapshot"
        (root / "extra.js").write_text("unexpected")
        with self.assertRaises(ValueError): verify_stored(self.queue.home / bid, self.spec)
        (root / "extra.js").unlink()
        (root / "widgets/src/join.js").write_text("changed")
        with self.assertRaises(ValueError): verify_stored(self.queue.home / bid, self.spec)

    def test_path_credentials_and_traversal_rejected(self):
        for path in ("../outside", ".env", ".ssh/id_ed25519", "widgets/credentials.json", "C:/host/file"):
            with self.assertRaises(ValueError): safe_path(self.root, path)

    def test_retry_limits_and_remote_endpoint_refused(self):
        for field, value in (("seconds", 901), ("units", 101)):
            spec = copy.deepcopy(self.spec)
            spec["limits"][field] = value
            with self.assertRaises(ValueError): self.queue.enqueue(spec, actor="lead", team_id="platform")
        spec = copy.deepcopy(self.spec)
        spec["lanes"]["lanes"]["lane-gpu-a"]["endpoint"]["base_url"] = "https://external.invalid"
        with self.assertRaises(ValueError): self.queue.enqueue(spec, actor="lead", team_id="platform")

    def test_terminal_retention_receipt_and_cancel(self):
        bid = self.enqueue()
        self.queue.cancel(bid)
        self.assertEqual(self.queue.rows()[0]["state"], "cancelled")
        with self.assertRaises(ValueError): self.queue.cancel(bid)
        self.queue.db.execute("UPDATE batches SET expires=0")
        self.queue.db.commit()
        self.assertEqual(self.queue.reap(), [bid])
        self.assertFalse((self.queue.home / bid).exists())
        self.assertTrue(self.queue.enqueue(self.spec, actor="lead", team_id="platform")["deduplicated"])

    def test_returned_acceptance_is_independently_gated(self):
        bid = self.enqueue()
        self.queue.state(bid, "pc-claimed")
        folder = self.queue.home / bid
        task = Store(folder / "engine/tasks" / bid[:16] / "factory.db")
        task.create_task(bid[:16], None, str(folder / "snapshot"), {})
        card = {"unit_id": "one", "task_id": bid[:16], "repo": "widgets", "kind": "doc_map",
                "difficulty": "easy", "target": {"file": "src/join.js", "symbols": ["join"]}}
        task.add_unit(card, 1)
        task.close()
        runner = SimpleNamespace(start=AsyncMock(), close=AsyncMock(),
                                 run=AsyncMock(return_value={"ok": False, "stage": "scope"}))
        engine = SimpleNamespace(runner=runner, repos={"widgets": SimpleNamespace(root=folder / "snapshot/widgets")},
                                 profiles={"widgets": {}}, _baselines=AsyncMock())
        work = self.root
        # remote expects its queue below WORK/queue, which is the state created above.
        with patch.object(remote, "WORK", work), patch("factory_engine.engine.Engine", return_value=engine):
            result = asyncio.run(remote.pc_return({"batch": bid, "units": [
                {"unit_id": "one", "status": "accepted", "output": "forged"}]}))
        self.assertEqual(result["counts"]["accepted"], 0)
        self.assertEqual(result["counts"]["bounced"], 1)
        self.assertEqual(self.queue.rows()[0]["state"], "quarantine")

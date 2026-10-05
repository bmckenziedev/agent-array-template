"""HTTP policy checks backed by the production durable batch store."""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "queue"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "engine"))
from factory_queue.api_store import BatchStore
from tests import test_api


class StoreIntegrationTests(test_api.APITests):
    def setUp(self):
        super().setUp()
        root = Path(self.temp.name)
        teams = [{"id": "alpha", "leads": ["lead"], "weight": 3,
                  "factory": {"queue_priority_ceiling": 5}},
                 {"id": "beta", "leads": ["other"], "weight": 2}]
        estates = {"estates": [{"id": "alpha-estate", "owner_team": "alpha",
                                "data_class": "restricted"},
                               {"id": "beta-estate", "owner_team": "beta",
                                "data_class": "internal"}]}
        for name, value in (("teams", teams), ("estates", estates)):
            (root / (name + ".json")).write_text(json.dumps(value), encoding="utf-8")
        self.store = BatchStore(root / "batches.sqlite", estates, teams)
        self.api.store = self.store
        self.last_id = None

    def tearDown(self):
        self.api_context.__exit__(None, None, None)
        self.store.close()
        self.log_context.__exit__(None, None, None)
        self.kube_context.__exit__(None, None, None)
        self.temp.cleanup()

    def submit(self, token="member", **extra):
        payload = {"estate_id": "alpha-estate", "template": {
            "template": 1, "template_id": "toy-docs", "kind": "doc_map",
            "expand": {"via": "find_undocumented", "repo": "toy", "roots": ["src"]},
            "card": {"difficulty": "easy"}, "profile": {"profile": 1}},
            "cards": [], "priority": 1, **extra}
        status, result = self.request("/v1/batches", token, payload)
        if status == 202:
            self.last_id = result["batch_id"]
        return status, result

    def request(self, path, token="lead", body=None):
        if self.last_id:
            path = path.replace("batch1", self.last_id)
        return super().request(path, token, body)

    def test_lead_approval_member_cancel_and_audit(self):
        self.assertEqual(self.submit()[0], 202)
        self.assertEqual(self.request("/v1/batches/batch1/approve", "member", {"x": 1})[0], 403)
        self.assertEqual(self.request("/v1/batches/batch1/approve", "lead", {"x": 1})[0], 200)
        event = self.store.events(self.last_id)[1]
        self.assertEqual((event["actor"], event["actor_kind"]), ("sub-lead", "lead"))
        self.assertEqual(self.request("/v1/batches/batch1/cancel", "member", {"x": 1})[0], 200)
        self.assertEqual(self.store.get(self.last_id)["status"], "cancelled")

    def test_directory_lead_revocation_is_immediate(self):
        self.submit()
        root = Path(self.temp.name)
        teams = json.loads((root / "teams.json").read_text())
        teams[0]["leads"] = []
        (root / "teams.json").write_text(json.dumps(teams), encoding="utf-8")
        self.assertEqual(self.request("/v1/batches/batch1/approve", "lead", {"x": 1})[0], 403)

    def test_list_does_not_return_payload(self):
        self.submit()
        _, value = self.request("/v1/batches", "member")
        self.assertNotIn("payload", value["batches"][0])

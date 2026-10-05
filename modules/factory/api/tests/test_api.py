"""Exercise HTTP and real TokenReview requests without a cluster or detached server."""

from contextlib import contextmanager, redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from factory_api.auth import Authenticator, Directory, Kubernetes
from factory_api.service import FactoryServer


@contextmanager
def serving(server):
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        assert not thread.is_alive()


class TokenReviewHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.reviews.append(payload)
        token = payload["spec"]["token"]
        slug = token.split("-")[0]
        status = {"authenticated": slug in ("lead", "member", "other", "suspended"),
                  "audiences": ["example-mcp"],
                  "user": {"username": "system:serviceaccount:aa-u-" + slug + ":session"}}
        if token == "wrong-audience":
            status = {"authenticated": True, "audiences": ["kubernetes"],
                      "user": {"username": "system:serviceaccount:aa-u-lead:session"}}
        if token == "oidc":
            status = {"authenticated": True, "audiences": ["example-mcp"],
                      "user": {"username": "oidc:sub-lead"}}
        if token == "admin-sa":
            status = {"authenticated": True, "audiences": ["example-mcp"],
                      "user": {"username": "system:serviceaccount:system:admin"}}
        if token in ("portal-oidc", "apiserver-sa"):
            if "audiences" in payload["spec"]:
                status = {"authenticated": False}
            else:
                status = {"authenticated": True, "audiences": ["kubernetes"],
                          "user": {"username": "oidc:sub-lead" if token == "portal-oidc" else
                                   "system:serviceaccount:aa-u-lead:session"}}
        self.reply({"status": status})

    def do_GET(self):
        slug = self.path.rsplit("/", 1)[-1].removeprefix("aa-u-")
        labels = {"example.org/kind": "user-sessions", "example.org/user": slug}
        if self.server.bad_namespace:
            labels["example.org/kind"] = "system"
        self.reply({"metadata": {"labels": labels}})

    def reply(self, payload):
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class Store:
    def __init__(self):
        self.rows = {}

    def submit(self, payload, identity):
        team = payload.get("team", identity["primary_team"])
        if payload.get("estate_id") != team + "-estate":
            raise PermissionError("estate")
        batch = {"batch_id": "batch1", "team_id": team, "status": "pending-approval",
                 "submitted_by": identity["user"]}
        self.rows["batch1"] = batch
        return batch

    def get(self, batch_id):
        return self.rows.get(batch_id)

    def list(self, team, limit=100):
        return [dict(row) for row in self.rows.values() if row["team_id"] == team][:limit]

    def approve(self, batch_id, identity):
        self.rows[batch_id]["status"] = "ready"
        self.rows[batch_id]["actor"] = identity["sub"]
        return dict(self.rows[batch_id])

    def cancel(self, batch_id, identity):
        self.rows[batch_id]["status"] = "cancelled"
        return dict(self.rows[batch_id])


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        users = [{"slug": slug, "oidc_sub": "sub-" + slug,
                  "oidc_subject": "oidc:sub-" + slug,
                  "teams": ["beta" if slug == "other" else "alpha"],
                  "primary_team": "beta" if slug == "other" else "alpha",
                  "status": "suspended" if slug == "suspended" else "active"}
                 for slug in ("lead", "member", "other", "suspended")]
        config = {"users": users, "teams": [{"id": "alpha", "leads": ["lead"]},
                                              {"id": "beta", "leads": ["other"]}],
                  "estates": {"estates": []},
                  "mcp-entitlements": {"alpha": ["factory"], "beta": ["factory"]}}
        for name, value in config.items():
            (root / (name + ".json")).write_text(json.dumps(value), encoding="utf-8")
        (root / "api-token").write_text("fake-api-identity", encoding="utf-8")
        self.kube = HTTPServer(("127.0.0.1", 0), TokenReviewHandler)
        self.kube.reviews = []
        self.kube.bad_namespace = False
        self.kube_context = serving(self.kube)
        kube_url = self.kube_context.__enter__()
        auth = Authenticator(Kubernetes(kube_url, root / "api-token"), Directory(root),
                             "example", "aa-u-", "example.org")
        self.store = Store()
        self.api = FactoryServer(("127.0.0.1", 0), auth, self.store)
        self.logs = io.StringIO()
        self.log_context = redirect_stdout(self.logs)
        self.log_context.__enter__()
        self.api_context = serving(self.api)
        self.url = self.api_context.__enter__()

    def tearDown(self):
        self.api_context.__exit__(None, None, None)
        self.log_context.__exit__(None, None, None)
        self.kube_context.__exit__(None, None, None)
        self.temp.cleanup()

    def request(self, path, token="lead", body=None):
        request = Request(self.url + path, data=json.dumps(body).encode() if body else None,
                          headers={"Authorization": "Bearer " + token,
                                   "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def submit(self, token="member", **extra):
        return self.request("/v1/batches", token, {"estate_id": "alpha-estate",
                            "template": {"kind": "doc_map"}, "cards": [], "priority": 1, **extra})

    def test_lead_approval_member_cancel_and_audit(self):
        self.assertEqual(self.submit()[0], 202)
        self.assertEqual(self.request("/v1/batches/batch1/approve", "member", {"x": 1})[0], 403)
        status, value = self.request("/v1/batches/batch1/approve", "lead", {"x": 1})
        self.assertEqual((status, value["status"]), (200, "ready"))
        self.assertEqual(self.store.rows["batch1"]["actor"], "sub-lead")
        self.assertEqual(self.request("/v1/batches/batch1/cancel", "member", {"x": 1})[0], 200)
        events = [json.loads(line) for line in self.logs.getvalue().splitlines()]
        self.assertTrue(any(e["outcome"] == "deny" for e in events))
        self.assertTrue(any(e["actor"]["sub"] == "sub-lead" for e in events))
        self.assertNotIn("alpha-estate", self.logs.getvalue())
        self.assertTrue(all(r["spec"]["audiences"] == ["example-mcp"] for r in self.kube.reviews))

    def test_other_team_cannot_read_approve_cancel_or_submit(self):
        self.submit()
        self.assertEqual(self.request("/v1/batches/batch1", "other")[0], 403)
        for action in ("approve", "cancel"):
            self.assertEqual(self.request("/v1/batches/batch1/" + action, "other", {"x": 1})[0], 403)
        self.assertEqual(self.submit("other", team="alpha")[0], 403)
        self.assertEqual(self.request("/v1/batches?team=alpha", "other")[0], 403)

    def test_identity_arguments_rejected(self):
        for field in ("submitted_by", "actor", "user", "data_class"):
            self.assertEqual(self.submit(**{field: "lead"})[0], 400)

    def test_auth_failures_and_oidc_mapping(self):
        for token in ("wrong-audience", "unknown", "admin-sa", "suspended"):
            self.assertEqual(self.submit(token)[0], 403)
        self.assertEqual(self.submit("oidc")[0], 202)
        self.kube.bad_namespace = True
        self.assertEqual(self.submit("lead")[0], 403)

    def test_portal_oidc_default_audience_cannot_admit_apiserver_service_account(self):
        self.assertEqual(self.submit("portal-oidc")[0], 202)
        self.assertEqual(self.submit("apiserver-sa")[0], 403)

    def test_entitlement_fail_closed_and_estate_boundary(self):
        root = Path(self.temp.name)
        (root / "mcp-entitlements.json").write_text('{"alpha": []}', encoding="utf-8")
        self.assertEqual(self.submit()[0], 403)
        (root / "mcp-entitlements.json").write_text('{"alpha": ["factory"]}', encoding="utf-8")
        self.assertEqual(self.submit(estate_id="beta-estate")[0], 403)

    def test_status_and_list_are_bounded(self):
        self.submit()
        self.assertEqual(self.request("/v1/batches/batch1", "member")[0], 200)
        status, value = self.request("/v1/batches?team=alpha", "member")
        self.assertEqual((status, value["limit"], len(value["batches"])), (200, 100, 1))
        self.assertEqual(self.request("/v1/batches/missing")[0], 404)


if __name__ == "__main__":
    unittest.main()

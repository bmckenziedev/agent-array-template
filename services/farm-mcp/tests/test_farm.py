import copy
import importlib.util
import json
import sys
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from farm_mcp import Farm, TOOLS
from aa_mcp import Application, Auth, make_server


class Directory:
    def __init__(self, model):
        self.model = model

    def load(self):
        return {"users": self.model["users"], "teams": self.model["teams"],
                "accounts": self.model["accounts"]["accounts"],
                "entitlements": {"payments": ["farm"], "platform": ["farm"]}}


class Kube:
    def __init__(self, label):
        self.label, self.calls = label, []
        self.sts_labels = {label + "/user": "ana", label + "/tool": "codex",
                           label + "/account": "acct-chatgpt-seat-ana"}

    def review(self, token, audience):
        return {"authenticated": token == "synthetic-pod-token", "audiences": [audience],
                "user": {"username": "system:serviceaccount:aa-u-ana:session"}}

    def namespace(self, name):
        return {"metadata": {"labels": {self.label + "/user": "ana", self.label + "/kind": "user-sessions"}}}

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if "/pods?" in path:
            return {"items": [{"metadata": {"name": "own-pod", "labels": {self.label + "/user": "ana"}},
                               "status": {"phase": "Running"}},
                              {"metadata": {"name": "other-pod", "labels": {self.label + "/user": "bo"}}}]}
        if path.endswith("/scale"):
            return body or {"metadata": {"resourceVersion": "3"}, "spec": {"replicas": 1}}
        return {"metadata": {"labels": self.sts_labels}}


class Backends:
    def __init__(self, model):
        self.model, self.calls = model, []
        self.seat_only = False
        self.fail_completion = False
        self.fail_mint = False

    def pace(self, method, path, body=None, token=None):
        self.calls.append(("pace", method, path, body, token))
        if path == "/v1/accounts":
            return [{"id": a["id"], "leases_active": 1, "type": a["type"], "windows": []}
                    for a in self.model["accounts"]["accounts"]]
        if path == "/v1/route":
            ids = ["acct-claude-seat-ana"] if self.seat_only else ["acct-local-gpu", "acct-anthropic-api-payments", "acct-openai-api-shared"]
            return {"accounts": [{"id": name, "vendor": "local", "headroom_pct": 50} for name in ids]}
        if path == "/v1/lease":
            return {"lease_id": "lease-example", "expires_at": "synthetic-expiry"}
        return {}

    def litellm(self, method, path, body=None, token=None):
        self.calls.append(("litellm", method, path, body, token))
        if path == "/key/generate":
            if self.fail_mint:
                raise RuntimeError("synthetic-mint-key")
            return {"key": "synthetic-call-key"}
        if path == "/v1/chat/completions":
            if self.fail_completion:
                raise RuntimeError("synthetic-call-key synthetic-pod-token private prompt")
            return {"choices": [{"message": {"content": "answer synthetic-call-key synthetic-mint-key synthetic-pod-token"}}]}
        return {}

    def factory(self, method, path, body=None, token=None):
        self.calls.append(("factory", method, path, body, token))
        if method == "GET":
            return {"batches": [{"batch_id": "batch-example", "status": "pending-approval", "team": "payments"},
                                {"batch_id": "other-batch", "status": "running", "team": "platform"}]}
        return {"batch_id": "batch-example", "status": "pending-approval"}


class FarmTests(unittest.TestCase):
    def setUp(self):
        self.model = json.loads((ROOT / "mcp/tests/fixtures/org.fixture.json").read_text())
        self.events = []
        label = self.model["keys"]["LABEL_PREFIX"]
        self.kube = Kube(label)
        self.backend = Backends(self.model)
        self.config = {"label_prefix": label, "factory_enabled": True,
                       "tiers": self.model["org"]["sessions"]["tiers"],
                       "estates": self.model["estates"]["estates"], "lanes": ["example-lane"]}
        self.auth = Auth(self.kube, Directory(self.model), "agent-array", "aa-u-", label, "farm")
        self.actor = self.auth.authenticate("Bearer synthetic-pod-token")
        self.farm = Farm(self.kube, self.backend.pace, self.backend.litellm, self.backend.factory,
                         "synthetic-mint-key", self.config)
        self.app = Application("farm", self.auth, TOOLS, self.farm.call, self.events.append)

    def rpc(self, tool, args):
        return self.app.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": tool, "arguments": args}}, "Bearer synthetic-pod-token")

    def llm_args(self, **changes):
        return {"prompt": "private prompt", "model": "local-coder", "data_class": "internal", **changes}

    def test_only_six_tools_no_local_or_seat_execution(self):
        self.assertEqual({t["name"] for t in TOOLS}, {"farm_status", "farm_llm", "farm_units",
                                                     "farm_batch", "farm_sessions", "farm_scale"})

    def test_sessions_and_accounts_only_owned_or_shared(self):
        status = self.farm.call("farm_status", {}, self.actor)
        self.assertEqual([p["name"] for p in status["sessions"]], ["own-pod"])
        accounts = {a["id"] for a in status["accounts"]}
        self.assertNotIn("acct-claude-seat-bo", accounts)
        self.assertNotIn("acct-claude-seat-cy", accounts)
        self.assertEqual(status["lanes"], ["example-lane"])
        self.assertEqual([b["batch_id"] for b in status["queue"]["batches"]], ["batch-example"])
        self.assertTrue(all("aa-u-ana" in p for _, p, _ in self.kube.calls))

    def test_scoped_mint_completion_redaction_and_cleanup(self):
        status, result = self.rpc("farm_llm", self.llm_args())
        self.assertEqual(status, 200)
        self.assertIn("result", result)
        mint = next(c for c in self.backend.calls if c[2] == "/key/generate")[3]
        self.assertEqual(mint["team_id"], "payments")
        self.assertEqual(mint["user_id"], "ana")
        self.assertEqual(mint["models"], ["local-coder"])
        self.assertEqual(mint["duration"], "120s")
        self.assertEqual(mint["metadata"]["account_id"], "acct-local-gpu")
        self.assertEqual(mint["metadata"]["client"], "farm-mcp")
        paths = [c[2] for c in self.backend.calls]
        self.assertEqual(paths, ["/v1/route", "/v1/lease", "/key/generate", "/v1/chat/completions",
                                 "/key/delete", "/v1/lease/lease-example"])
        for value in ("synthetic-call-key", "synthetic-mint-key", "synthetic-pod-token", "private prompt"):
            self.assertNotIn(value, json.dumps(self.events))
        for value in ("synthetic-call-key", "synthetic-mint-key", "synthetic-pod-token"):
            self.assertNotIn(value, json.dumps(result))

    def test_completion_failure_deletes_key_and_lease(self):
        self.backend.fail_completion = True
        result = self.rpc("farm_llm", self.llm_args())[1]
        self.assertIn("error", result)
        paths = [c[2] for c in self.backend.calls]
        self.assertIn("/key/delete", paths)
        self.assertEqual(paths[-1], "/v1/lease/lease-example")
        self.assertNotIn("synthetic-call-key", json.dumps(result) + json.dumps(self.events))

    def test_mint_failure_releases_lease(self):
        self.backend.fail_mint = True
        self.rpc("farm_llm", self.llm_args())
        self.assertEqual(self.backend.calls[-1][2], "/v1/lease/lease-example")

    def test_seat_is_never_routed(self):
        self.backend.seat_only = True
        result = self.rpc("farm_batch", {"tasks": [self.llm_args()]})[1]
        self.assertIn("seats are interactive", json.dumps(result))
        self.assertEqual([c[2] for c in self.backend.calls], ["/v1/route"])

    def test_team_data_class_model_and_vendor_denials(self):
        for changes in ({"team": "platform"}, {"data_class": "restricted"}, {"model": "openai-api-standard"},
                        {"model": "claude-api-standard", "data_class": "restricted"}):
            self.assertIn("error", self.rpc("farm_llm", self.llm_args(**changes))[1])
        self.assertFalse(any(c[2] == "/key/generate" for c in self.backend.calls))

    def test_entitlement_denial(self):
        self.model["teams"][0]["mcp_servers"] = []
        self.assertEqual(self.rpc("farm_status", {})[0], 403)
        self.assertEqual(self.events[-1]["outcome"], "deny")

    def test_validation_rejects_local_paths_modes_and_wrong_types(self):
        cases = [("farm_llm", self.llm_args(workdir="/tmp/private")),
                 ("farm_llm", self.llm_args(max_tokens=True)),
                 ("farm_batch", {"tasks": [self.llm_args(mode="local")]}),
                 ("farm_batch", {"tasks": ["not-an-object"]}),
                 ("farm_scale", {"statefulset": "codex-node-b", "replicas": -1})]
        for tool, args in cases:
            self.assertIn("error", self.rpc(tool, args)[1])
        self.assertEqual(self.backend.calls, [])

    def test_factory_submission_primary_team_and_priority(self):
        result = self.rpc("farm_units", {"estate_id": "payments-core", "cards": [{"repo": "payments-api"}], "priority": 100})[1]
        self.assertIn("pending-approval", json.dumps(result))
        call = self.backend.calls[-1]
        self.assertEqual(call[2], "/v1/batches")
        self.assertEqual(call[3]["template"], "doc_map")
        self.assertEqual(call[3]["priority"], 50)
        self.assertEqual(call[4], "synthetic-pod-token")

    def test_factory_disabled_and_wrong_estate(self):
        self.config["factory_enabled"] = False
        self.assertIn("factory module is disabled", json.dumps(self.rpc("farm_units", {"estate_id": "payments-core", "cards": [{}]})[1]))
        self.config["factory_enabled"] = True
        self.assertIn("error", self.rpc("farm_units", {"estate_id": "platform-infra", "cards": [{}]})[1])
        self.assertEqual(self.backend.calls, [])

    def test_scale_clamps_to_account_and_tier(self):
        result = self.farm.scale({"statefulset": "codex-node-b", "replicas": 100}, self.actor)
        self.assertEqual(result["replicas"], 1)
        self.assertEqual(self.kube.calls[-1][0], "PUT")
        self.assertEqual(self.kube.calls[-1][2]["metadata"]["resourceVersion"], "3")
        self.assertEqual(self.farm.scale({"statefulset": "codex-node-b", "replicas": 0}, self.actor)["replicas"], 0)

    def test_scale_rejects_other_owner_and_account(self):
        for changes in ({self.kube.label + "/user": "bo"}, {self.kube.label + "/account": "acct-claude-seat-bo"}):
            old = self.kube.sts_labels.copy()
            self.kube.sts_labels.update(changes)
            with self.assertRaises(ValueError):
                self.farm.scale({"statefulset": "codex-node-b", "replicas": 2}, self.actor)
            self.kube.sts_labels = old
        self.assertFalse(any(m == "PUT" for m, _, _ in self.kube.calls))

    def test_scale_tier_is_stricter_than_account(self):
        actor = self.auth.authenticate("Bearer synthetic-pod-token")
        actor["user"] = self.model["users"][1]
        actor["namespace"] = "aa-u-bo"
        self.kube.sts_labels = {self.kube.label + "/user": "bo", self.kube.label + "/tool": "claude",
                               self.kube.label + "/account": "acct-claude-seat-bo"}
        self.assertEqual(self.farm.scale({"statefulset": "claude-node-b", "replicas": 100}, actor)["replicas"], 2)

    def test_pace_failure_does_not_mint_or_call(self):
        self.farm.pace = lambda *a: (_ for _ in ()).throw(RuntimeError("private upstream error"))
        result = self.rpc("farm_llm", self.llm_args())[1]
        self.assertIn("error", result)
        self.assertEqual(self.backend.calls, [])

    def test_key_delete_failure_still_releases_lease(self):
        original = self.farm.litellm
        def backend(method, path, body=None, token=None):
            if path == "/key/delete":
                raise RuntimeError("synthetic-call-key")
            return original(method, path, body, token)
        self.farm.litellm = backend
        result = self.rpc("farm_llm", self.llm_args())[1]
        self.assertIn("error", result)
        self.assertEqual(self.backend.calls[-1][2], "/v1/lease/lease-example")
        self.assertNotIn("synthetic-call-key", json.dumps(result) + json.dumps(self.events))

    def test_batch_uses_pace_and_external_leases_for_each_call(self):
        result = self.rpc("farm_batch", {"tasks": [self.llm_args(), self.llm_args()]})[1]
        self.assertIn("result", result)
        self.assertEqual(sum(c[2] == "/v1/route" for c in self.backend.calls), 2)
        self.assertEqual(sum(c[2] == "/v1/lease" for c in self.backend.calls), 2)

    def test_in_process_server(self):
        server = make_server(self.app, ("127.0.0.1", 0))
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        try:
            message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "farm_sessions", "arguments": {}}}
            request = Request(f"http://127.0.0.1:{server.server_port}/mcp", json.dumps(message).encode(),
                              {"Authorization": "Bearer synthetic-pod-token"})
            with urlopen(request) as response:
                self.assertIn("own-pod", response.read().decode())
        finally:
            server.shutdown()
            worker.join(5)
            server.server_close()
            self.assertFalse(worker.is_alive())

    def test_templates_render_and_scope_rbac(self):
        import yaml
        import re
        for path in (ROOT / "services/farm-mcp/k8s").rglob("*.tmpl.yaml"):
            scopes = self.model["entities"]["user"] if ".per-user." in path.name else [{}]
            for entity in scopes:
                keys = {**self.model["keys"], **entity}
                text = re.sub(r"\{\{([A-Z][A-Z0-9_]*)\}\}", lambda m: keys[m[1]], path.read_text())
                for doc in yaml.safe_load_all(text):
                    if doc["kind"] == "Deployment":
                        pod = doc["spec"]["template"]
                        self.assertEqual(pod["metadata"]["labels"][keys["LABEL_PREFIX"] + "/llm-client"], "true")
                    if doc["kind"] == "RoleBinding":
                        self.assertEqual(doc["metadata"]["namespace"], entity["USER_NS"])
                        self.assertEqual(doc["subjects"][0]["namespace"], keys["NS_SYSTEM"])

    def test_config_plugin_and_all_api_endpoints(self):
        path = ROOT / "services/farm-mcp/render_plugin.py"
        spec = importlib.util.spec_from_file_location("farm_renderer", path)
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        output = {}
        self.model["keys"]["APISERVER_ENDPOINT_IPS_JSON"] = '["100.64.0.10","100.64.0.11"]'
        plugin.render(self.model, lambda path, text: output.__setitem__(path, text))
        cm = json.loads(output["global/services/farm-mcp/config.yaml"])
        config = json.loads(cm["data"]["config.json"])
        self.assertEqual(config["tiers"], self.config["tiers"])
        self.assertEqual(config["factory_enabled"], self.model["org"]["modules"]["factory"]["enabled"])
        self.assertIn("100.64.0.11/32", output["global/services/farm-mcp/k8s/api-endpoints.yaml"])


if __name__ == "__main__":
    unittest.main()

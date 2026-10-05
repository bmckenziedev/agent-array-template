import copy
import hashlib
import importlib.util
import json
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("mcp_render", ROOT / "render_plugin.py")
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.model = json.loads((ROOT / "tests/fixtures/org.fixture.json").read_text())

    def rendered(self, model=None):
        output = {}
        plugin.render(model or self.model, lambda path, text: output.__setitem__(path, text))
        return output

    def cm(self, slug, tool):
        return json.loads(self.rendered()[f"users/{slug}/mcp/{tool}-mcp.yaml"])

    def test_ana_both_tools_and_hashes(self):
        for tool in ("claude", "codex"):
            cm = self.cm("ana", tool)
            self.assertEqual(cm["metadata"]["namespace"], "aa-u-ana")
            rendered = json.loads(cm["data"]["mcp-rendered.json"])
            self.assertEqual(rendered["servers"], ["factory", "farm", "tickets"])
            self.assertEqual(set(rendered["sha256"]), set(cm["data"]) - {"mcp-rendered.json"})
            for key, digest in rendered["sha256"].items():
                self.assertEqual(hashlib.sha256(cm["data"][key].encode()).hexdigest(), digest)

    def test_factory_url_follows_module_activation(self):
        self.model["org"]["modules"]["factory"]["enabled"] = True
        entry = json.loads(self.cm("ana", "claude")["data"]["managed-mcp.json"])["mcpServers"]["factory"]
        self.assertEqual(entry["env"]["FACTORY_API_URL"], "http://factory-api." + self.model["keys"]["NS_FACTORY"] + ".svc:8080")

    def test_claude_transport_contract(self):
        entries = json.loads(self.cm("ana", "claude")["data"]["managed-mcp.json"])["mcpServers"]
        self.assertEqual(entries["factory"]["type"], "stdio")
        self.assertEqual(entries["farm"], {"type": "http", "url": "http://farm-mcp.agent-array-system.svc:8080/mcp",
                                         "headersHelper": "/usr/local/bin/aa-mcp-token"})
        self.assertEqual(entries["tickets"]["url"], "http://mcp-tickets.agent-array-mcp.svc:8080/mcp")

    def test_codex_identity_and_config(self):
        try:
            import tomllib
        except ImportError:
            self.skipTest("TOML parser check requires Python 3.11+; renderer supports 3.10")
        data = self.cm("ana", "codex")["data"]
        req = tomllib.loads(data["requirements.mcp.toml"])["mcp_servers"]
        cfg = tomllib.loads(data["managed_config.mcp.toml"])["mcp_servers"]
        self.assertEqual(req["factory"]["identity"], {"command": "/opt/factory/venv/bin/python"})
        self.assertEqual(cfg["farm"]["command"], "/usr/local/bin/aa-mcp-bridge")
        self.assertEqual(cfg["farm"]["args"], ["http://farm-mcp.agent-array-system.svc:8080/mcp"])
        self.assertNotIn("bearer_token_env_var", cfg["farm"])
        self.assertEqual(cfg["factory"]["tool_timeout_sec"], 960)

    def test_bo_hermes_filtered_and_suspended_config_kept(self):
        for slug in ("bo", "cy"):
            data = self.cm(slug, "claude")["data"]
            self.assertEqual(json.loads(data["mcp-rendered.json"])["servers"], ["factory", "farm", "tickets"])
        self.assertFalse(any("kimi-mcp" in p for p in self.rendered()))

    def test_http_only_network_policy_and_selectors(self):
        output = self.rendered()
        paths = [p for p in output if "netpol-mcp" in p]
        self.assertEqual(len(paths), 6)
        for path in paths:
            policy = json.loads(output[path])
            self.assertEqual(policy["spec"]["policyTypes"], ["Egress"])
            self.assertTrue(path.endswith(("farm.yaml", "tickets.yaml")))
            peer = policy["spec"]["egress"][0]["to"][0]
            self.assertIn("podSelector", peer)
            self.assertIn("namespaceSelector", peer)

    def test_context_filtered_to_estate_owner_and_vendor(self):
        data = json.loads(self.rendered()["users/ana/mcp/context-policy.yaml"])["data"]
        estates = json.loads(data["estates.json"])
        self.assertEqual([e["id"] for e in estates], ["payments-core"])
        self.assertEqual(estates[0]["snapshot_targets"], ["claude"])
        self.assertEqual(estates[0]["deny_globs"], sorted(self.model["estates"]["deny_globs"]))
        self.assertEqual([s["name"] for s in json.loads(data["sources.json"])], ["estate-index", "estate-snapshot", "tickets"])

    def test_repeat_is_byte_identical(self):
        self.assertEqual(self.rendered(), self.rendered())

    def test_offboarded_and_vendor_disabled(self):
        self.model["users"][0]["status"] = "offboarded"
        self.model["org"]["vendors"]["anthropic"]["enabled"] = False
        output = self.rendered()
        self.assertFalse(any(p.startswith("users/ana/") for p in output))
        self.assertFalse(any(p.endswith("claude-mcp.yaml") for p in output))

    def test_refuse_kimi_and_secret_env(self):
        for change in (lambda s: s["clients"].append("kimi"), lambda s: s["env"].update({"API_TOKEN": "not-a-value"})):
            model = copy.deepcopy(self.model)
            change(model["mcp_registry"]["servers"][0])
            with self.assertRaises(ValueError):
                self.rendered(model)

    def test_allowed_teams_intersect_union(self):
        next(s for s in self.model["mcp_registry"]["servers"] if s["name"] == "tickets")["allowed_teams"] = ["platform"]
        self.assertNotIn("tickets", json.loads(self.cm("ana", "claude")["data"]["mcp-rendered.json"])["servers"])

    def test_directory_is_owned_by_skeleton(self):
        output = self.rendered()
        self.assertFalse(any("org-directory" in p or "entitlements" in p for p in output))

    def test_all_mcp_templates_render(self):
        for path in (ROOT / "k8s").rglob("*.tmpl.yaml"):
            scopes = self.model["entities"]["mcp"] if ".per-mcp." in path.name else [{}]
            for entity in scopes:
                text = plugin.subst(path.read_text(), {**self.model["keys"], **entity})
                for doc in yaml.safe_load_all(text):
                    self.assertIn("kind", doc)
                    if doc["kind"] == "Deployment":
                        pod = doc["spec"]["template"]["spec"]
                        self.assertTrue(pod["securityContext"]["runAsNonRoot"])
                        self.assertTrue(pod["containers"][0]["securityContext"]["readOnlyRootFilesystem"])
                        self.assertIn("@sha256:", pod["containers"][0]["image"])

    def test_server_optional_secret_and_all_api_endpoints(self):
        server = next(s for s in self.model["mcp_registry"]["servers"] if s["name"] == "tickets")
        server.pop("server_secret")
        self.model["entities"]["mcp"][0]["MCP_SERVER_SECRET_NAME"] = ""
        self.model["keys"]["APISERVER_ENDPOINT_IPS_JSON"] = '["100.64.0.10","100.64.0.11"]'
        output = self.rendered()
        deployment = yaml.safe_load(output["mcp/tickets/mcp/k8s/server/deployment.yaml"])
        self.assertFalse(any(v["name"] == "upstream" for v in deployment["spec"]["template"]["spec"]["volumes"]))
        self.assertIn("100.64.0.11/32", output["mcp/tickets/mcp/k8s/server/networkpolicy.yaml"])

    def test_missing_upstream_means_no_public_egress(self):
        next(s for s in self.model["mcp_registry"]["servers"] if s["name"] == "tickets")["server_egress"] = []
        self.assertNotIn("0.0.0.0/0", self.rendered()["mcp/tickets/mcp/k8s/server/networkpolicy.yaml"])


if __name__ == "__main__":
    unittest.main()

import copy
import importlib.util
import json
from pathlib import Path
import unittest

import yaml
import render_plugin as plugin

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    return json.loads((ROOT / "tests/fixtures/org.fixture.json").read_text())


def rendered(model):
    output = {}
    plugin.render(model, lambda path, text: output.__setitem__(path, text))
    return output


class RenderTests(unittest.TestCase):
    def test_api_accounts_only_and_determinism(self):
        model = fixture()
        output = rendered(model)
        self.assertEqual(output, rendered(model))
        config = json.loads(json.loads(output["global/llm/k8s/litellm-config.yaml"])["data"]["config.yaml"])
        self.assertEqual({m["model_name"] for m in config["model_list"]},
                         {"claude-api-standard", "openai-api-standard"})
        self.assertNotIn("seat", output["files/llm/provider-env.json"])
        env = json.loads(output["files/llm/provider-env.json"])
        self.assertEqual(env[0]["name"], "AA_PROVIDER_ACCT_ANTHROPIC_API_PAYMENTS")
        self.assertEqual(env[0]["valueFrom"]["secretKeyRef"],
                         {"name": "llm-provider-anthropic-payments", "key": "api_key"})
        self.assertEqual(env[1]["valueFrom"]["secretKeyRef"]["name"], "llm-provider-openai-shared")
        self.assertFalse(config["general_settings"]["store_prompts_in_spend_logs"])
        self.assertTrue(config["general_settings"]["enforce_fallback_model_access"])

    def test_teams_and_active_members(self):
        data = json.loads(rendered(fixture())["files/llm/teams.json"])
        teams = {t["team_id"]: t for t in data["teams"]}
        self.assertEqual(teams["payments"]["max_budget"], 1500)
        self.assertEqual(teams["platform"]["max_budget"], 3000)
        self.assertEqual([m["user_id"] for m in teams["payments"]["members_with_roles"]], ["ana", "bo"])
        self.assertEqual([u["user_id"] for u in data["users"]], ["ana", "bo"])
        self.assertEqual(teams["payments"]["metadata"]["task_budget_usd"]["standard"], 5)

    def test_enabled_lanes_and_fallback(self):
        model = fixture()
        model["org"]["modules"]["gpu-lanes"] = {"enabled": True, "lanes": [
            {"name": "lane-gpu-a", "model_group": "local-coder", "slots": 1},
            {"name": "lane-gpu-b", "model_group": "local-coder-small", "slots": 3}]}
        output = rendered(model)
        config = json.loads(json.loads(output["global/llm/k8s/litellm-config.yaml"])["data"]["config.yaml"])
        self.assertEqual(len(config["model_list"]), 4)
        self.assertEqual(config["router_settings"]["fallbacks"], [{"local-coder": ["local-coder-small"]}])
        self.assertEqual(config["model_list"][-1]["litellm_params"]["max_parallel_requests"], 3)
        model["org"]["components"]["llm"] = {"fallbacks": {"local-coder": ["claude-api-standard"]}}
        with self.assertRaises(ValueError):
            rendered(model)

    def test_vendor_and_namespace_fail_closed(self):
        model = fixture()
        model["org"]["components"]["llm"] = {"models": {"claude-api-standard": "openai/gpt-4.1"}}
        with self.assertRaises(ValueError):
            rendered(model)
        model = fixture()
        model["accounts"]["accounts"][-3]["secret"]["namespace"] = "portal"
        with self.assertRaises(ValueError):
            rendered(model)

    def test_templates_parse_and_network_selectors(self):
        model = fixture()
        docs = []
        for path in sorted((ROOT / "k8s").glob("*.tmpl.yaml")):
            docs.extend(yaml.safe_load_all(plugin.subst(path.read_text(), model["keys"])))
        output = rendered(model)
        docs.extend(yaml.safe_load(output[p]) for p in output if p.startswith("global/"))
        self.assertTrue(all(d["metadata"]["namespace"] == model["keys"]["NS_LLM"] for d in docs))
        policies = {d["metadata"]["name"]: d for d in docs if d["kind"] == "NetworkPolicy"}
        sources = policies["litellm-allow"]["spec"]["ingress"][0]["from"]
        self.assertEqual(len(sources), 5)
        for source in sources:
            self.assertEqual(source["podSelector"]["matchLabels"],
                             {model["keys"]["LABEL_PREFIX"] + "/llm-client": "true"})
            self.assertIn("namespaceSelector", source)
        for obj in docs:
            if obj["kind"] in ("Deployment", "StatefulSet"):
                pod = obj["spec"]["template"]["spec"]
                self.assertFalse(pod["automountServiceAccountToken"])
                self.assertTrue(pod["securityContext"]["runAsNonRoot"])
                self.assertIn("@sha256:", pod["containers"][0]["image"])
                refs = pod["containers"][0].get("env", [])
                if obj["metadata"]["name"] != "litellm":
                    self.assertFalse(any(e["name"].startswith("AA_PROVIDER") for e in refs))
            if obj["kind"] == "Service":
                self.assertEqual(obj["spec"]["type"], "ClusterIP")

    def test_unknown_placeholder(self):
        with self.assertRaises(KeyError):
            plugin.subst("{{UNKNOWN}}", {})

    def test_api_fallback_cycle(self):
        model = fixture()
        account = model["accounts"]["accounts"][-2]
        account["litellm_models"] = ["api-a", "api-b", "api-c"]
        model["org"]["components"]["llm"] = {
            "models": {g: "openai/gpt-4.1" for g in account["litellm_models"]},
            "fallbacks": {"api-a": ["api-b"], "api-b": ["api-c"], "api-c": ["api-a"]}}
        with self.assertRaisesRegex(ValueError, "cycle"):
            rendered(model)

    def test_duplicate_account_group(self):
        model = fixture()
        account = copy.deepcopy(model["accounts"]["accounts"][-3])
        account["id"] = "another-api"
        model["accounts"]["accounts"].append(account)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            rendered(model)

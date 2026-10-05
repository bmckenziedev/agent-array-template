import copy
import importlib.util
import unittest

import yaml

from tests.test_templates import ROOT, FIXTURE


class RenderPlugin(unittest.TestCase):
    def render(self, model):
        spec = importlib.util.spec_from_file_location(
            "session_render", ROOT / "render_plugin.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        output = {}
        module.render(
            model, lambda path, text: output.update({path: yaml.safe_load(text)})
        )
        return output

    def test_every_user_tool_has_distinct_retained_node_affine_pv(self):
        output = self.render(FIXTURE)
        pvs = [obj for obj in output.values() if obj["kind"] == "PersistentVolume"]
        enabled = [
            e
            for e in FIXTURE["entities"]["user_tool"]
            if e["TOOL"] != "kimi" or FIXTURE["org"]["vendors"]["moonshot"]["enabled"]
        ]
        self.assertEqual(len(pvs), len(enabled))
        self.assertEqual(len({obj["metadata"]["name"] for obj in pvs}), len(enabled))
        for entity in enabled:
            pv = next(
                obj
                for obj in pvs
                if obj["metadata"]["name"]
                == entity["USER_NS"] + "-" + entity["TOOL_HOME_CLAIM"]
            )
            self.assertEqual(
                pv["spec"]["claimRef"],
                {"namespace": entity["USER_NS"], "name": entity["TOOL_HOME_CLAIM"]},
            )
            self.assertEqual(pv["spec"]["persistentVolumeReclaimPolicy"], "Retain")
            self.assertEqual(
                pv["spec"]["nodeAffinity"]["required"]["nodeSelectorTerms"][0][
                    "matchExpressions"
                ][0]["values"],
                [entity["TOOL_HOME_NODE"]],
            )
            self.assertEqual(
                pv["spec"]["local"]["path"],
                "/".join(
                    [
                        FIXTURE["keys"]["LOGIN_HOST_ROOT"],
                        entity["USER_SLUG"],
                        entity["TOOL"],
                        entity["TOOL_HOME_NODE"],
                    ]
                ),
            )

    def test_public_egress_has_deny_cidrs_and_tool_selector(self):
        output = self.render(FIXTURE)
        for entity in FIXTURE["entities"]["user_tool"]:
            if FIXTURE["keys"][entity["TOOL"].upper() + "_EGRESS"] != "public-443":
                continue
            obj = output[
                "users/"
                + entity["USER_SLUG"]
                + "/sessions/vendor-egress-"
                + entity["TOOL"]
                + ".yaml"
            ]
            self.assertEqual(
                obj["spec"]["podSelector"]["matchLabels"][
                    FIXTURE["keys"]["LABEL_PREFIX"] + "/tool"
                ],
                entity["TOOL"],
            )
            self.assertEqual(
                obj["spec"]["egress"][0]["to"][0]["ipBlock"]["except"],
                sorted(set(__import__("json").loads(
                    FIXTURE["keys"]["SESSION_EGRESS_DENY_CIDRS_JSON"]
                ) + ["0.0.0.0/8", "224.0.0.0/4", "240.0.0.0/4"])),
            )
            self.assertEqual(
                obj["spec"]["egress"][0]["ports"], [{"protocol": "TCP", "port": 443}]
            )

    def test_disabled_moonshot_never_emits_kimi_entities(self):
        model = copy.deepcopy(FIXTURE)
        entity = dict(model["entities"]["user_tool"][0])
        entity.update(
            ENTITY_ID="ana/kimi",
            TOOL="kimi",
            TOOL_HOME_CLAIM="kimi-home-node-a",
            TOOL_ACCOUNT_ID="synthetic-kimi-seat",
        )
        model["entities"]["user_tool"].append(entity)
        model["org"]["vendors"]["moonshot"]["enabled"] = False
        output = self.render(model)
        self.assertFalse(any("kimi" in path for path in output))
        model["org"]["vendors"]["moonshot"]["enabled"] = True
        output = self.render(model)
        self.assertIn("global/sessions/k8s/login-pv-ana-kimi.yaml", output)

"""Fixture rendering checks computed identity and narrow read permissions."""

import importlib.util
import json
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
MODEL = json.loads((ROOT / "tests/fixtures/org.fixture.json").read_text(encoding="utf-8"))
KEY_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")


def subst(text, keys):
    def rep(match):
        if match[1] not in keys:
            raise KeyError("unknown placeholder " + match[1])
        return str(keys[match[1]])
    return KEY_RE.sub(rep, text)


class TemplateTests(unittest.TestCase):
    def test_client_deployment_fields(self):
        keys = dict(MODEL["keys"], C_AA_CA_BUNDLE_PATH="~/cluster-ca.pem", C_AA_PANEL_URL="")
        data = json.loads(subst((ROOT / "client.tmpl.json").read_text(), keys))
        from aa_cli.config import validate
        self.assertEqual(validate(data)["label_prefix"], keys["LABEL_PREFIX"])

    def test_reader_subjects_are_fixture_groups(self):
        template = ROOT / "k8s/client-directory-reader.per-team.tmpl.yaml"
        subjects = []
        for entity in MODEL["entities"]["team"]:
            role, binding = list(yaml.safe_load_all(subst(template.read_text(), dict(MODEL["keys"], **entity))))
            self.assertEqual(role["rules"][0]["resourceNames"], ["aa-client-directory"])
            self.assertEqual(role["rules"][0]["verbs"], ["get"])
            subjects.append(binding["subjects"][0]["name"])
        self.assertEqual(subjects, [e["TEAM_OIDC_GROUP"] for e in MODEL["entities"]["team"]])

    def test_no_per_user_cluster_role(self):
        self.assertFalse((ROOT / "k8s/namespace-reader.per-user.tmpl.yaml").exists())

    def test_directory_plugin_identity_matches_entities(self):
        spec = importlib.util.spec_from_file_location("aa_render", ROOT / "render_plugin.py")
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        emitted = {}
        plugin.render(MODEL, lambda path, data: emitted.setdefault(path, data))
        cm = json.loads(emitted["global/tools/aa/k8s/client-directory.yaml"])
        identities = json.loads(cm["data"]["identities.json"])
        for entity in MODEL["entities"]["user"]:
            identity = identities[entity["USER_OIDC_SUBJECT"]]
            self.assertEqual(identity["namespace"], entity["USER_NS"])
            self.assertEqual(identity["max_replicas_per_tool"], int(entity["TIER_MAX_REPLICAS_PER_TOOL"]))

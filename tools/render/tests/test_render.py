"""Contract tests using isolated repositories; no components are required."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from aa_render import model, yamlsub
from aa_render.cli import main, render
from aa_render.lint import key_sets, lint
from aa_render.plugins import safe_path
from aa_render.templates import RenderError, condition, subst

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
FIXTURE = json.loads((HERE / "fixtures/org.fixture.json").read_text(encoding="utf-8"))


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def dump(value, indent=0):
    def scalar(v):
        return json.dumps(v, ensure_ascii=False)

    lines = []
    prefix = " " * indent
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (dict, list)) and item:
                lines.append(prefix + str(key) + ":")
                lines.append(dump(item, indent + 2).rstrip())
            else:
                lines.append(prefix + str(key) + ": " + scalar(item))
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)) and item:
                lines.append(prefix + "-")
                lines.append(dump(item, indent + 2).rstrip())
            else:
                lines.append(prefix + "- " + scalar(item))
    return "\n".join(lines) + "\n"


class RepoTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="aa-render-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        shutil.copytree(REPO / "org", self.root / "org")
        shutil.copytree(HERE / "fixtures/mcp", self.root / "mcp")
        self.org = self.root / "org/org.example.yaml"
        self.model = model.load_model(self.root, self.org)

    def cli(self, *args):
        with (
            contextlib.redirect_stdout(io.StringIO()) as stdout,
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            code = main(["--root", str(self.root), "--org", "org/org.example.yaml", *args])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_fixture_exact(self):
        self.assertEqual(self.model, FIXTURE)

    def test_defaults_merge_and_flatten(self):
        write(
            self.root / "monitoring/org.component.defaults.yaml",
            "scope: components\nname: monitoring\ndefaults:\n  retention: 15\n  nested: {a: 1, b: 2}\n  list: [x, y]\n",
        )
        doc = yamlsub.load(self.org)
        doc["components"] = {"monitoring": {"nested": {"b": 9}}}
        write(self.org, dump(doc))
        write(
            self.root / "modules/factory/org.component.defaults.yaml",
            "scope: modules\nname: factory\ndefaults: {enabled: true, replicas: 2}\n",
        )
        result = model.load_model(self.root, self.org)
        self.assertEqual(result["keys"]["C_MONITORING_NESTED_B"], "9")
        self.assertEqual(result["keys"]["C_MONITORING_NESTED_A"], "1")
        self.assertEqual(result["keys"]["C_MONITORING_LIST_JSON"], '["x","y"]')
        self.assertEqual(result["keys"]["M_FACTORY_ENABLED"], "false")
        self.assertEqual(result["keys"]["M_FACTORY_REPLICAS"], "2")

    def test_templates_all_scopes_and_tool_filter(self):
        write(self.root / "sample/k8s/global.tmpl.yaml", 'name: "{{PROJECT_NAME}}"\n')
        write(self.root / "sample/helm/release/values.yaml", "replicas: 1\n")
        write(self.root / "sample/k8s/static.yaml", "kind: ConfigMap\n")
        write(self.root / "sample/env.tmpl.txt", "{{ORG_NAME}}")
        for scope in ("user", "user-tool", "team", "node", "mcp", "account"):
            write(self.root / f"sample/k8s/entity.per-{scope}.tmpl.yaml", 'id: "{{ENTITY_ID}}"\n')
        write(self.root / "sessions/claude/k8s/tool.per-user-tool.tmpl.yaml", 'tool: "{{TOOL}}"\n')
        outputs = render(self.root, self.model)
        self.assertIn("global/sample/k8s/global.yaml", outputs)
        self.assertTrue(outputs["global/sample/k8s/static.yaml"].endswith(b"kind: ConfigMap\n"))
        self.assertTrue(outputs["global/sample/k8s/static.yaml"].startswith(b"# Rendered by"))
        self.assertTrue(outputs["files/sample/helm/release/values.yaml"].endswith(b"replicas: 1\n"))
        self.assertIn("files/sample/env.txt", outputs)
        for scope, group in (
            ("user", "users"),
            ("team", "teams"),
            ("node", "nodes"),
            ("mcp", "mcp"),
            ("account", "accounts"),
        ):
            for entity in self.model["entities"][scope]:
                self.assertIn(f"{group}/{entity['ENTITY_ID']}/sample/k8s/entity.yaml", outputs)
        self.assertIn("users/ana/sample/k8s/codex/entity.yaml", outputs)
        self.assertIn("users/ana/sessions/claude/k8s/claude/tool.yaml", outputs)
        self.assertNotIn("users/ana/sessions/claude/k8s/codex/tool.yaml", outputs)

    def test_conditions_nested_and_module_gating(self):
        self.assertTrue(condition("org.vendors.anthropic.enabled", self.model))
        self.assertFalse(condition("org.vendors.moonshot.enabled", self.model))
        self.assertTrue(condition("org.network.access.kind == oidc-proxy", self.model))
        self.assertTrue(condition("org.network.access.kind != none", self.model))
        write(self.root / "outer/RENDER-IF", "org.vendors.anthropic.enabled")
        write(self.root / "outer/yes/RENDER-IF", "org.network.access.kind == oidc-proxy")
        write(self.root / "outer/yes/a.tmpl.txt", "ok")
        write(self.root / "outer/no/RENDER-IF", "org.vendors.moonshot.enabled")
        write(self.root / "outer/no/a.tmpl.txt", "no")
        write(self.root / "modules/factory/a.tmpl.txt", "no")
        outputs = render(self.root, self.model)
        self.assertIn("files/outer/yes/a.txt", outputs)
        self.assertNotIn("files/outer/no/a.txt", outputs)
        self.assertNotIn("files/modules/factory/a.txt", outputs)
        self.model["org"]["modules"]["factory"]["enabled"] = True
        self.assertIn("files/modules/factory/a.txt", render(self.root, self.model))

    def test_unknown_key_and_lbrace(self):
        self.assertEqual(
            subst("{{LBRACE2}}UNKNOWN}} {{ .Labels }}", self.model["keys"]), "{{" + "UNKNOWN}} {{ .Labels }}"
        )
        with self.assertRaisesRegex(RenderError, "unknown placeholder"):
            subst("{{" + "UNKNOWN}}", self.model["keys"])

    def test_plugin_restriction(self):
        for path in ("/global/a.yaml", "../bad", "global/../bad", "C:/bad", "global\\bad"):
            with self.subTest(path=path), self.assertRaises(RenderError):
                safe_path(path)
        write(
            self.root / "sample/render_plugin.py",
            'PLUGIN_NAME = "sample"\ndef render(model, emit):\n    emit("global/sample/k8s/a.yaml", "kind: ConfigMap")\n',
        )
        self.assertIn("global/sample/k8s/a.yaml", render(self.root, self.model))
        write(
            self.root / "sample/render_plugin.py",
            'PLUGIN_NAME = "sample"\ndef render(model, emit):\n    emit("global/elsewhere/a.yaml", "kind: ConfigMap")\n',
        )
        with self.assertRaisesRegex(RenderError, "outside plugin directory"):
            render(self.root, self.model)

    def test_org_directory(self):
        shutil.copyfile(REPO / "tools/render/render_plugin.py", self.root / "render_plugin.py")
        # Contract paths belong to tools/render, so put the plugin there.
        (self.root / "render_plugin.py").unlink()
        write(
            self.root / "tools/render/render_plugin.py", (REPO / "tools/render/render_plugin.py").read_text()
        )
        outputs = render(self.root, self.model)
        entries = [k for k in outputs if "org-directory-" in k]
        self.assertEqual(len(entries), 5)
        for key in entries:
            doc = yamlsub.parse_yaml_subset(outputs[key].decode())
            self.assertEqual(doc["metadata"]["name"], "org-directory")
            self.assertIn(
                doc["metadata"]["namespace"],
                [self.model["keys"]["NS_" + n] for n in ("SYSTEM", "MCP", "PORTAL", "LLM", "FACTORY")],
            )
            self.assertEqual(
                set(doc["data"]),
                {
                    "users.json",
                    "teams.json",
                    "accounts.json",
                    "estates.json",
                    "mcp-entitlements.json",
                    "context-sources.json",
                },
            )
            self.assertEqual(json.loads(doc["data"]["accounts.json"]), self.model["accounts"]["accounts"])
            self.assertEqual(json.loads(doc["data"]["estates.json"]), self.model["estates"])
            self.assertNotIn("api_key_value", outputs[key].decode())

    def test_secrets_index(self):
        write(
            self.root / "sample/secrets.required.yaml",
            "version: 1\nsecrets:\n  - name: example-secret\n    namespace_ref: user\n    keys: [API_KEY]\n    purpose: Example authentication\n    recipe: provision externally\n    required_when: always\n  - name: disabled-secret\n    namespace_ref: llm\n    keys: [API_KEY]\n    purpose: Disabled\n    recipe: provision externally\n    required_when: org.vendors.moonshot.enabled\n",
        )
        outputs = render(self.root, self.model)
        records = json.loads(outputs["files/SECRETS-REQUIRED.json"])
        self.assertEqual(len(records), 3)
        self.assertEqual({r["namespace"] for r in records}, {"aa-u-ana", "aa-u-bo", "aa-u-cy"})
        self.assertIn(b"Example authentication", outputs["files/SECRETS-REQUIRED.md"])

    def test_cli_strict_check_cleanup_safety(self):
        self.assertEqual(self.cli("--strict", "--validate-only")[0], 2)
        self.assertEqual(self.cli("--validate-only")[0], 0)
        write(self.root / "sample/a.tmpl.txt", "{{PROJECT_NAME}}")
        self.assertEqual(self.cli()[0], 0)
        self.assertEqual(self.cli("--check")[0], 0)
        write(self.root / "rendered/stale.txt", "stale")
        code, output, _ = self.cli("--check")
        self.assertEqual(code, 1)
        self.assertIn("stale.txt", output)
        self.assertTrue((self.root / "rendered/stale.txt").exists())
        self.assertEqual(self.cli()[0], 0)
        self.assertFalse((self.root / "rendered/stale.txt").exists())
        write(self.root / "unsafe/important.txt", "keep")
        self.assertEqual(self.cli("--out", "unsafe")[0], 2)
        self.assertEqual((self.root / "unsafe/important.txt").read_text(), "keep")
        self.assertEqual(self.cli("--out", str(self.root))[0], 2)

    def test_lint_and_list_keys(self):
        write(self.root / "sample/a.per-user.tmpl.yaml", "{{USER_SLUG}} {{ORG_NAME}}")
        write(self.root / "sample/README.md", "{{TOOL}}")
        self.assertEqual(lint([self.root / "sample"], self.model), [])
        write(self.root / "sample/b.tmpl.yaml", "{{USER_SLUG}}")
        write(self.root / "sample/bad.md", "{{" + "BOGUS}}")
        self.assertEqual(len(lint([self.root / "sample"], self.model)), 1)
        self.assertEqual(self.cli("--lint-placeholders", str(self.root / "sample"))[0], 1)
        sets = key_sets(self.model)
        expected = set(FIXTURE["keys"]) | {
            k for scope in FIXTURE["entities"].values() for e in scope for k in e
        }
        self.assertEqual(set.union(*sets.values()), expected)
        code, output, _ = self.cli("--list-keys")
        self.assertEqual(code, 0)
        self.assertEqual(set(output.splitlines()), expected)
        self.assertEqual(self.cli("--list-keys", "--scope", "user", "--markdown")[0], 0)

    def test_cli_subprocess_synthetic(self):
        write(self.root / "sample/k8s/a.tmpl.yaml", 'kind: ConfigMap\nmetadata: {name: "{{PROJECT_NAME}}"}\n')
        result = subprocess.run(
            [
                sys.executable,
                "-S",
                str(REPO / "tools/render/render.py"),
                "--org",
                "org/org.example.yaml",
                "--root",
                str(self.root),
                "--out",
                str(self.root / "output"),
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "output/global/sample/k8s/a.yaml").is_file())

    def test_strict_auto_warning_and_success(self):
        org = yamlsub.load(self.org)
        for image in org["images"].values():
            image["digest"] = "sha256:" + "a" * 64
        write(self.org, dump(org))
        code, _, error = self.cli("--strict", "--validate-only")
        self.assertEqual(code, 2)
        self.assertIn("home_node auto", error)
        users_path = self.root / org["files"]["users"]
        users = yamlsub.load(users_path)
        users["users"][2]["tools"]["claude"]["home_node"] = "node-a"
        write(users_path, dump(users))
        self.assertEqual(self.cli("--strict", "--validate-only")[0], 0)

    def test_offboarding_and_deterministic_json(self):
        users_path = self.root / self.model["org"]["files"]["users"]
        users = yamlsub.load(users_path)
        users["users"][0]["status"] = "offboarded"
        write(users_path, dump(users))
        changed = model.load_model(self.root, self.org)
        self.assertNotIn("ana", [u["USER_SLUG"] for u in changed["entities"]["user"]])
        self.assertNotIn("ana", [t["USER_SLUG"] for t in changed["entities"]["user_tool"]])
        self.assertEqual(
            next(t for t in changed["entities"]["user_tool"] if t["USER_SLUG"] == "cy")["TOOL_REPLICAS"], "0"
        )
        outputs = render(self.root, changed)
        self.assertEqual(outputs, render(self.root, changed))
        self.assertFalse(outputs["files/SECRETS-REQUIRED.json"].startswith(b"#"))

    def test_unknown_scope_collision_and_invalid_condition(self):
        self.assertFalse(condition("org.missing", self.model))
        write(self.root / "sample/a.per-invalid.tmpl.yaml", "kind: ConfigMap")
        with self.assertRaisesRegex(RenderError, "unknown scope"):
            render(self.root, self.model)
        (self.root / "sample/a.per-invalid.tmpl.yaml").unlink()
        write(self.root / "sample/a.tmpl.txt", "ok")
        write(
            self.root / "sample/render_plugin.py",
            'PLUGIN_NAME = "collision"\ndef render(model, emit):\n    emit("files/sample/a.txt", "duplicate")\n',
        )
        with self.assertRaisesRegex(RenderError, "duplicate output"):
            render(self.root, self.model)

    def test_missing_org(self):
        self.org.unlink()
        code, _, error = self.cli()
        self.assertEqual(code, 2)
        self.assertIn("copy org/org.example.yaml", error)

    def test_component_zero_image_digest_is_strict(self):
        org = yamlsub.load(self.org)
        org["components"] = {"supervisor": {"image": "registry.example.org/project/supervisor@sha256:" + "0" * 64}}
        write(self.org, dump(org))
        with self.assertRaisesRegex(model.OrgError, "C_SUPERVISOR_IMAGE.*all-zero digest"):
            model.load_model(self.root, self.org, strict=True)

    def test_username_claim_sub_required(self):
        org = yamlsub.load(self.org)
        org["identity"]["oidc"]["username_claim"] = "email"
        write(self.org, dump(org))
        with self.assertRaisesRegex(model.OrgError, "username_claim must be sub"):
            model.load_model(self.root, self.org)

    def test_unknown_node_runtime_rejected(self):
        org = yamlsub.load(self.org)
        org["nodes"][0]["runtime_classes"].append("unconfigured-runtime")
        write(self.org, dump(org))
        with self.assertRaisesRegex(model.OrgError, "runtime_classes must use configured"):
            model.load_model(self.root, self.org)

    def test_string_comparison_and_missing_paths(self):
        self.assertTrue(condition('org.version == "1"', {"org": {"version": 1}}))
        self.assertFalse(condition("org.missing != true", self.model))

    def test_gated_plugin_is_never_imported(self):
        write(self.root / "disabled/RENDER-IF", "org.missing")
        write(self.root / "disabled/render_plugin.py", "raise RuntimeError('must not import')\n")
        self.assertNotIn("global/disabled", render(self.root, self.model))


    def test_optional_forge_identity_is_normalised_and_cli_only(self):
        users = self.root / "org/users.example.yaml"
        users.write_text(users.read_text().replace('    oidc_sub: "00u1example0ana0000"',
            '    git: {credential_secret: forge-ana, provider: github, username: example-login}\n    oidc_sub: "00u1example0ana0000"'))
        write(self.root / "sessions/org.component.defaults.yaml", (REPO / "sessions/org.component.defaults.yaml").read_text())
        normalized = model.load_model(self.root, self.org)
        user = next(u for u in normalized["users"] if u["slug"] == "ana")
        self.assertEqual(user["git"]["credential_secret"], "forge-ana")
        entity = next(u for u in normalized["entities"]["user_tool"] if u["ENTITY_ID"] == "ana/claude")
        import yaml
        text = subst((REPO / "sessions/claude/k8s/statefulset.per-user-tool.tmpl.yaml").read_text(),
                     dict(normalized["keys"], **entity), "session")
        pod = yaml.safe_load(text)["spec"]["template"]["spec"]
        volume = next(v for v in pod["volumes"] if v["name"] == "git-credential")
        self.assertEqual(volume["secret"]["secretName"], "forge-ana")
        for container in pod["containers"]:
            mounts = [m for m in container.get("volumeMounts", []) if m["name"] == "git-credential"]
            self.assertEqual(bool(mounts), container["name"] == "claude")
            if mounts:
                self.assertTrue(mounts[0]["readOnly"])


class ParserTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("yaml"), "PyYAML not installed")
    def test_pyyaml_parity(self):
        import yaml

        paths = list((REPO / "org").glob("*.example.yaml")) + list((HERE / "fixtures/mcp").glob("*.yaml"))
        snippets = [
            'a: [a, "b", 3]\n',
            'a: {k: v, k2: "v"}\n',
            "a:\n  - {k: v}\n  - x\n",
            "a: 'it''s # literal'\nb: \"a\\\"b\\n\\t\\\\\" # comment\n",
            "a: true\nb: false\nc: null\nd: -42\ne: ~\nf:\n",
            "a:\n  - x: 1\n    nested:\n      a: b\n  - x: 2\n",
        ]
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(yamlsub.load(path), yaml.safe_load(path.read_text()))
        for text in snippets:
            with self.subTest(text=text):
                self.assertEqual(yamlsub.parse_yaml_subset(text), yaml.safe_load(text))

    def test_subset_errors_located(self):
        bad = [
            "a: 1.2",
            "a: yes",
            "a: no",
            "a: on",
            "a: off",
            "a: 12:30",
            "a: 2026-01-01",
            "a: &anchor x",
            "a: *anchor",
            "a: !tag x",
            "a: |",
            "a: >",
            "a: [a, [b]]",
            "a: {x: {y: z}}",
            "a:\n\tb: x",
            "a: 1\na: 2",
            "a:\n  b: 1\n c: 2",
            'a: "multi\nline"',
            'a: "bad\\q"',
            "a: {x: 1, x: 2}",
        ]
        for text in bad:
            with (
                self.subTest(text=text),
                self.assertRaisesRegex(yamlsub.YamlError, r"snippet:[0-9]+:[0-9]+:"),
            ):
                yamlsub.parse_yaml_subset(text, "snippet")

    @unittest.skipUnless(importlib.util.find_spec("jsonschema"), "jsonschema not installed")
    def test_schemas(self):
        import jsonschema

        for path in sorted((REPO / "org").glob("*.example.yaml")):
            schema = json.loads(
                (REPO / "org/schema" / (path.name.split(".")[0] + ".schema.json")).read_text()
            )
            jsonschema.Draft202012Validator.check_schema(schema)
            jsonschema.validate(yamlsub.load(path), schema)


class AdditionalParserTests(unittest.TestCase):
    def test_quoted_colon_and_indentless_lists(self):
        text = "a:\n- 'a: b'\n-    key: value\n     nested: true\nb: end\n"
        self.assertEqual(
            yamlsub.parse_yaml_subset(text), {"a": ["a: b", {"key": "value", "nested": True}], "b": "end"}
        )


class ErrorLocationTests(unittest.TestCase):
    def test_duplicate_inline_mapping_location(self):
        with self.assertRaisesRegex(yamlsub.YamlError, r"example:3:5: duplicate key"):
            yamlsub.parse_yaml_subset("items:\n  - key: first\n    key: duplicate\n", "example")

    def test_signed_times_are_rejected(self):
        for text in ("a: -1:20", "a: 120:30", "a: 12:30.5"):
            with self.subTest(text=text), self.assertRaisesRegex(yamlsub.YamlError, "quote sexagesimal"):
                yamlsub.parse_yaml_subset(text)

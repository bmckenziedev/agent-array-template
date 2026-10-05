"""Render actual policy templates, merge, and run each image's fail-closed check."""

import importlib.machinery
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_policy_merge import load
from tests.test_templates import ROOT, rendered


class PolicyIntegration(unittest.TestCase):
    def test_every_user_tool_policy_passes_image_check(self):
        merge = load("aa-policy-merge")
        for path, entity, obj in rendered():
            if obj["kind"] != "ConfigMap" or not obj["metadata"]["name"].endswith(
                "-policy"
            ):
                continue
            tool = entity["TOOL"]
            with self.subTest(
                entity=entity["ENTITY_ID"]
            ), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                base = directory / "base"
                base.mkdir()
                for name, text in obj["data"].items():
                    (base / name).write_text(text)
                output = directory / "out"
                merge.merge(tool, base, directory / "absent-mcp", output)
                if tool in {"claude", "codex"}:
                    target = (
                        output / "managed-settings.json" if tool == "claude" else output
                    )
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(ROOT / tool / "image/bin/policy-check.py"),
                            str(target),
                        ],
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    if tool == "codex":
                        import tomllib

                        requirements = tomllib.loads(
                            (output / "requirements.toml").read_text()
                        )
                        managed = tomllib.loads(
                            (output / "managed_config.toml").read_text()
                        )
                        mode = entity["USER_PERMISSION_MODE"]
                        approval = (
                            "never" if mode == "bypassPermissions" else "on-request"
                        )
                        self.assertEqual(
                            requirements["allowed_approval_policies"], [approval]
                        )
                        self.assertEqual(managed["approval_policy"], approval)
                        self.assertEqual(
                            "danger-full-access"
                            in requirements["allowed_sandbox_modes"],
                            mode == "bypassPermissions",
                        )
                else:
                    import tomllib

                    loader = importlib.machinery.SourceFileLoader(
                        "kimi_config", str(ROOT / "kimi/image/bin/aa-kimi-config")
                    )
                    spec = importlib.util.spec_from_loader(loader.name, loader)
                    module = importlib.util.module_from_spec(spec)
                    loader.exec_module(module)
                    self.assertEqual(
                        module.policy_problems(
                            tomllib.loads((output / "policy.toml").read_text())
                        ),
                        [],
                    )

    def test_codex_modes_cannot_escalate_beyond_team_policy(self):
        import tomllib

        merge = load("aa-policy-merge")
        data = next(
            obj["data"]
            for _, _, obj in rendered()
            if obj["kind"] == "ConfigMap" and obj["metadata"]["name"] == "codex-policy"
        )
        for mode in ("default", "acceptEdits", "plan", "bypassPermissions"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                base = directory / "base"
                base.mkdir()
                for name, text in data.items():
                    (base / name).write_text(
                        text.replace(
                            'approval_policy = "default"',
                            'approval_policy = "' + mode + '"',
                        )
                    )
                output = directory / "out"
                merge.merge("codex", base, directory / "absent", output)
                requirements = tomllib.loads((output / "requirements.toml").read_text())
                managed = tomllib.loads((output / "managed_config.toml").read_text())
                bypass = mode == "bypassPermissions"
                self.assertEqual(
                    requirements["allowed_approval_policies"],
                    ["never" if bypass else "on-request"],
                )
                expected = (
                    ["read-only"]
                    if mode == "plan"
                    else ["read-only", "workspace-write"]
                )
                if bypass:
                    expected.append("danger-full-access")
                self.assertEqual(requirements["allowed_sandbox_modes"], expected)
                self.assertNotIn("sandbox_mode", managed)

    def test_synthetic_kimi_modes_render_merge_and_validate(self):
        import copy
        import tomllib
        import yaml
        from tests.test_templates import FIXTURE, subst

        merge = load("aa-policy-merge")
        loader = importlib.machinery.SourceFileLoader(
            "kimi_modes", str(ROOT / "kimi/image/bin/aa-kimi-config")
        )
        spec = importlib.util.spec_from_loader(loader.name, loader)
        validator = importlib.util.module_from_spec(spec)
        loader.exec_module(validator)
        fixture = copy.deepcopy(FIXTURE)
        entity = dict(fixture["entities"]["user_tool"][0])
        entity.update(
            ENTITY_ID="ana/kimi",
            TOOL="kimi",
            TOOL_VENDOR="moonshot",
            TOOL_ACCOUNT_ID="synthetic-kimi-seat",
            TOOL_STS_NAME="kimi-node-a",
            TOOL_HOME_CLAIM="kimi-home-node-a",
            TOOL_IMAGE=fixture["keys"]["IMAGE_SESSION_KIMI"],
        )
        policy_path = next((ROOT / "kimi").rglob("policy.per-user-tool.tmpl.yaml"))
        sts_path = next((ROOT / "kimi").rglob("statefulset.per-user-tool.tmpl.yaml"))
        defaults = yaml.safe_load((ROOT / "org.component.defaults.yaml").read_text())[
            "defaults"
        ]
        component_keys = {
            "C_SESSIONS_" + name.upper(): str(value) for name, value in defaults.items()
        }
        for mode in ("default", "acceptEdits", "plan", "bypassPermissions"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                entity["USER_PERMISSION_MODE"] = mode
                keys = {**fixture["keys"], **component_keys, **entity}
                policy = yaml.safe_load(subst(policy_path.read_text(), keys))
                statefulset = yaml.safe_load(subst(sts_path.read_text(), keys))
                self.assertEqual(policy["metadata"]["namespace"], entity["USER_NS"])
                self.assertEqual(
                    statefulset["metadata"]["name"], entity["TOOL_STS_NAME"]
                )
                pod = statefulset["spec"]["template"]["spec"]
                for container in pod["containers"] + pod.get("initContainers", []):
                    self.assertRegex(container["image"], r"@sha256:[a-f0-9]{64}$")
                self.assertEqual(
                    [
                        volume["persistentVolumeClaim"]["claimName"]
                        for volume in pod["volumes"]
                        if "persistentVolumeClaim" in volume
                    ],
                    [entity["TOOL_HOME_CLAIM"]],
                )
                directory = Path(temp)
                base = directory / "base"
                base.mkdir()
                for name, text in policy["data"].items():
                    (base / name).write_text(text)
                output = directory / "out"
                merge.merge("kimi", base, directory / "absent", output)
                managed = tomllib.loads((output / "policy.toml").read_text())
                self.assertEqual(
                    managed["default_permission_mode"],
                    "auto" if mode == "bypassPermissions" else "manual",
                )
                self.assertEqual(managed["default_plan_mode"], mode == "plan")
                self.assertEqual(validator.policy_problems(managed), [])
        self.assertEqual(
            FIXTURE["entities"]["user_tool"], fixture["entities"]["user_tool"]
        )

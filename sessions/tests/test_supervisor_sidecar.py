"""Mounts, process boundaries and exact variant additions."""
import copy
import re
import unittest
import yaml
from pathlib import Path
from .test_templates import rendered, subst, ROOT, FIXTURE


class Sidecar(unittest.TestCase):
    def objects(self, enabled):
        objects = {(e["USER_SLUG"], e["TOOL"]): o for _, e, o in rendered(enabled)
                   if o["kind"] == "StatefulSet"}
        keys = {**FIXTURE["keys"], **FIXTURE["entities"]["user_tool"][0]}
        defaults = yaml.safe_load((ROOT / "org.component.defaults.yaml").read_text())["defaults"]
        keys.update({"C_SESSIONS_" + name.upper(): str(value) for name, value in defaults.items()})
        keys.update(TOOL="kimi", TOOL_IMAGE=FIXTURE["keys"]["IMAGE_SESSION_KIMI"])
        variant = "sts-supervised" if enabled else "sts-plain"
        path = ROOT / "kimi" / variant / "k8s/statefulset.per-user-tool.tmpl.yaml"
        objects[(keys["USER_SLUG"], "kimi")] = yaml.safe_load(subst(path.read_text(), keys))
        return objects

    def test_invariant_holder_only_variants(self):
        self.assertEqual(self.objects(True).keys(), self.objects(False).keys())
        for enabled in [True, False]:
            rows = [(e["USER_SLUG"], e["TOOL"]) for _, e, o in rendered(enabled) if o["kind"] == "StatefulSet"]
            self.assertEqual(len(rows), len(set(rows)))

    def test_invariant_no_credential_access(self):
        for identity, obj in self.objects(True).items():
            spec = obj["spec"]["template"]["spec"]
            self.assertIs(spec["shareProcessNamespace"], False)
            self.assertEqual(spec["runtimeClassName"], "kata")
            cli = next(c for c in spec["containers"] if c["name"] == identity[1])
            sup = next(c for c in spec["containers"] if c["name"] == "supervisor")
            sc = sup["securityContext"]
            self.assertEqual((sc["runAsUser"], sc["runAsGroup"]), (1000, 1000))
            self.assertEqual(sc["capabilities"], {"drop": ["ALL"]})
            self.assertEqual(sc["seccompProfile"], {"type": "RuntimeDefault"})
            self.assertTrue(sc["readOnlyRootFilesystem"])
            self.assertFalse(sc["allowPrivilegeEscalation"])
            mounts = {m["name"]: m for m in sup["volumeMounts"]}
            self.assertNotIn("home", mounts)
            if identity[1] == "kimi":
                self.assertNotIn("login", mounts)
            else:
                self.assertEqual(mounts["login"]["subPath"], "projects" if identity[1] == "claude" else "sessions")
                self.assertTrue(mounts["login"]["readOnly"])
            for volume in spec["volumes"]:
                self.assertNotIn("secret", volume)
                if volume["name"] == "aa-tmux":
                    self.assertEqual(volume["emptyDir"], {"medium": "Memory", "sizeLimit": "8Mi"})
            cli_env = {e["name"]: e.get("value") for e in cli["env"]}
            self.assertEqual(cli_env["TINI_SUBREAPER"], "1")
            self.assertEqual(cli_env["TMUX_TMPDIR"], "/run/aa-tmux")
            for container in spec["containers"]:
                self.assertNotIn("ports", container)
                self.assertRegex(container["image"], r"@sha256:[0-9a-f]{64}$")

    def test_invariant_session_cannot_use_supervisor(self):
        for obj in self.objects(True).values():
            spec = obj["spec"]["template"]["spec"]
            for volume in ["supervisor-run", "supervisor-token", "supervisor-hook-token"]:
                consumers = [c["name"] for c in spec["containers"] + spec.get("initContainers", [])
                             if any(m["name"] == volume for m in c.get("volumeMounts", []))]
                self.assertEqual(consumers, ["supervisor"])
            audiences = [s["serviceAccountToken"]["audience"] for v in spec["volumes"]
                         for s in v.get("projected", {}).get("sources", []) if "serviceAccountToken" in s]
            self.assertTrue(all(a.endswith(("-mcp", "-pace", "-supervisor", "-supervisor-hook")) for a in audiences))
            consumers = [c["name"] for c in spec["containers"] if any(m["name"] == "aa-tmux" for m in c.get("volumeMounts", []))]
            self.assertCountEqual(consumers, ["supervisor", obj["metadata"]["labels"][FIXTURE["keys"]["LABEL_PREFIX"] + "/tool"]])

    def test_structural_diff_only_documented_additions(self):
        plain, supervised = self.objects(False), self.objects(True)
        extra_env = {"AA_SUPERVISOR", "TMUX_TMPDIR", "TINI_SUBREAPER", "GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"}
        extra_vol = {"aa-tmux", "aa-shared", "supervisor-run", "supervisor-tmp", "supervisor-policy", "supervisor-token", "supervisor-hook-token"}
        for key, original in plain.items():
            stripped = copy.deepcopy(supervised[key])
            spec = stripped["spec"]["template"]["spec"]
            spec["containers"] = [c for c in spec["containers"] if c["name"] != "supervisor"]
            cli = next(c for c in spec["containers"] if c["name"] == key[1])
            # Strip only prepended entries, preserving any pre-existing registry git env.
            self.assertEqual(len(cli["env"][:7]), 7)
            self.assertEqual({e["name"] for e in cli["env"][:7]}, extra_env)
            cli["env"] = cli["env"][7:]
            cli["volumeMounts"] = [m for m in cli["volumeMounts"] if m["name"] not in {"aa-tmux", "aa-shared"}]
            spec["volumes"] = [v for v in spec["volumes"] if v["name"] not in extra_vol]
            self.assertEqual(stripped, original)

    def test_entrypoints_never_signal_or_test_pid_one(self):
        for tool in ["claude", "codex", "kimi"]:
            text = (ROOT / tool / "image/bin/entrypoint.sh").read_text()
            self.assertNotRegex(text, r"kill\s+(?:-[A-Za-z0-9]+\s+)?1\b|/proc/1/|\$\$\s*(?:==|-eq)\s*1")

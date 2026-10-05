"""Offline vendor guard/config checks ported from the source Kimi suite."""
import importlib.machinery
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest

HERE = Path(__file__).resolve().parent
BIN = HERE.parent / "bin"


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class KimiOffline(unittest.TestCase):
    def test_guard_source_cases(self):
        guard = load("aa_kimi_guard", BIN / "aa-kimi-guard")
        rules = guard.load_rules(str(HERE / "fixtures" / "policy.toml"))
        for tool, args, expected in guard.SELF_TEST:
            with self.subTest(tool=tool, args=args):
                result = guard.check({"tool_name": tool, "tool_input": args,
                                      "cwd": "/work/tasks"}, rules)
                self.assertEqual(result is not None, expected)

    def test_malformed_hook_fails_closed(self):
        result = subprocess.run([sys.executable, str(BIN / "aa-kimi-guard"),
                                 str(HERE / "fixtures" / "policy.toml")],
                                input="not json", text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)

    def test_policy_invariants_and_team_modes(self):
        config = load("aa_kimi_config", BIN / "aa-kimi-config")
        _, policy = config.load_policy(HERE / "fixtures" / "policy.toml")
        for mode in ("manual", "auto", "yolo"):
            policy["default_permission_mode"] = mode
            self.assertEqual(config.policy_problems(policy), [])
        policy["permission"]["rules"] = []
        self.assertTrue(config.policy_problems(policy))


if __name__ == "__main__":
    unittest.main()

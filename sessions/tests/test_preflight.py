import importlib.machinery
import importlib.util
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class Preflight(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "common/bin/entrypoint-check"
        loader = importlib.machinery.SourceFileLoader("preflight", str(path))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        self.module = importlib.util.module_from_spec(spec)
        loader.exec_module(self.module)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)

    def verify(self, uid=1000, mode=0o40700, storage="disk", observed="ext4"):
        info = types.SimpleNamespace(st_uid=uid, st_mode=mode)
        with patch.object(Path, "is_symlink", return_value=False), patch.object(
            Path, "is_dir", return_value=True
        ), patch.object(Path, "stat", return_value=info), patch.object(
            self.module.os, "getuid", return_value=1000, create=True
        ), patch.object(
            self.module, "filesystem", return_value=observed
        ), patch.dict(
            os.environ, {"AA_LOGIN_STORAGE": storage}
        ):
            self.module.verify_login(self.home)

    def test_only_holder_uid_and_0700_are_accepted(self):
        self.verify()
        for uid, mode in [(1001, 0o40700), (1000, 0o40755), (1000, 0o40710)]:
            with self.subTest(uid=uid, mode=mode), self.assertRaisesRegex(
                ValueError, "pod uid with mode 0700"
            ):
                self.verify(uid=uid, mode=mode)

    def test_storage_type_matches_declared_mode(self):
        self.verify(storage="tmpfs", observed="tmpfs")
        for storage, observed in [
            ("disk", "tmpfs"),
            ("tmpfs", "ext4"),
            ("disk", "rootfs"),
            ("invalid", "ext4"),
        ]:
            with self.subTest(storage=storage, observed=observed), self.assertRaises(
                ValueError
            ):
                self.verify(storage=storage, observed=observed)

    def test_vendor_env_refused_without_printing_value(self):
        for key in [
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "CLAUDE_CODE_OAUTH_TOKEN",
            "KIMI_API_KEY",
        ]:
            with patch.dict(os.environ, {key: "synthetic-secret"}, clear=True):
                with self.assertRaises(ValueError) as result:
                    self.module.verify_env("claude")
                self.assertNotIn("synthetic-secret", str(result.exception))

    def test_project_mcp_file_refused(self):
        (self.home / ".mcp.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "unrendered"):
            self.module.verify_mcp(self.home, self.home)

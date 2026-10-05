"""The receiver applies entitlement even when a client sends a hostile archive."""
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .test_policy_merge import load


class SnapshotSecurity(unittest.TestCase):
    def setUp(self):
        self.module = load("aa-snapshot")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.policy = Path(self.temp.name) / "estates.json"
        self.env = patch.dict(os.environ, {"AA_CONTEXT_POLICY": str(self.policy), "AA_TOOL": "claude"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.manifest = {"estate": "payments-core", "target": "claude", "repos": [
            {"name": "payments-api", "origin": "git@github.com:example-org/payments-api.git"}]}
        self.files = {"payments-api/src/main.py": {}}

    def write_policy(self):
        self.policy.write_text(json.dumps([{"id": "payments-core", "data_class": "internal",
                                           "snapshot_targets": ["claude"],
                                           "repos": ["github.com/example-org/payments-api"],
                                           "deny_globs": ["*secret*", "*.pem"]}]))

    def test_absent_policy_refuses_push(self):
        with self.assertRaises(self.module.SnapError):
            self.module.authorize_manifest(self.manifest, self.files)

    def test_entitled_repo_accepts_vendor_origin_spelling(self):
        self.write_policy()
        self.module.authorize_manifest(self.manifest, self.files)

    def test_other_repo_or_tool_refused(self):
        self.write_policy()
        self.manifest["repos"][0]["origin"] = "git@github.com:example-org/unentitled.git"
        with self.assertRaises(self.module.SnapError):
            self.module.authorize_manifest(self.manifest, self.files)
        self.manifest["repos"][0]["origin"] = "github.com/example-org/payments-api"
        self.manifest["target"] = "codex"
        with self.assertRaises(self.module.SnapError):
            self.module.authorize_manifest(self.manifest, self.files)

    def test_denied_path_and_undeclared_repo_refused(self):
        self.write_policy()
        for files in ({"payments-api/secret.txt": {}}, {"other/src.py": {}}):
            with self.assertRaises(self.module.SnapError):
                self.module.authorize_manifest(self.manifest, files)

    def test_agent_config_and_path_escape_refused_server_side(self):
        for name in ("repo/.mcp.json", "repo/.claude/settings.json", "repo/.codex/config.toml",
                     "repo/.kimi-code/mcp.json", "repo/.agents/skills/x", "../escape", "/escape"):
            with self.subTest(name=name), self.assertRaises(self.module.SnapError):
                self.module._safe_member_path(name)

    def test_archive_links_are_refused(self):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            entry = tarfile.TarInfo("repo/link")
            entry.type = tarfile.SYMTYPE
            entry.linkname = "/etc/passwd"
            archive.addfile(entry)
        stream.seek(0)
        with self.assertRaises(self.module.SnapError):
            self.module._extract(stream, Path(self.temp.name) / "incoming")

import hashlib
import importlib.machinery
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


def load(name):
    path = Path(__file__).resolve().parents[1] / "common/bin" / name
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class PolicyMerge(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / "base"
        self.mcp = self.root / "mcp"
        self.out = self.root / "out"
        self.base.mkdir()
        self.mcp.mkdir()
        (self.base / "managed-settings.base.json").write_text(
            json.dumps(
                {
                    "allowedMcpServers": [{"serverName": "stale"}],
                    "permissions": {"deny": ["Read(auth)"]},
                }
            )
        )
        self.module = load("aa-policy-merge")

    def fragment(self, servers, definitions=None, allowed=None):
        values = {
            "managed-mcp.json": {
                "mcpServers": (
                    definitions
                    if definitions is not None
                    else {name: {"url": "https://mcp.example.org"} for name in servers}
                )
            },
            "allowed-mcp-servers.json": (
                allowed
                if allowed is not None
                else [{"serverName": name} for name in servers]
            ),
        }
        hashes = {}
        for name, value in values.items():
            raw = json.dumps(value).encode()
            (self.mcp / name).write_bytes(raw)
            hashes[name] = hashlib.sha256(raw).hexdigest()
        (self.mcp / "mcp-rendered.json").write_text(
            json.dumps({"servers": servers, "sha256": hashes})
        )

    def run_merge(self):
        self.module.merge("claude", self.base, self.mcp, self.out)
        return json.loads((self.out / "managed-settings.json").read_text())

    def test_missing_fragment_means_no_mcp(self):
        policy = self.run_merge()
        self.assertEqual(policy["allowedMcpServers"], [])
        self.assertTrue(policy["allowManagedMcpServersOnly"])
        self.assertEqual(policy["permissions"]["deny"], ["Read(auth)"])
        self.assertEqual(json.loads((self.out / "managed-mcp.json").read_text()), {"mcpServers": {}})

    def test_allowlist_is_exact_and_hash_covers_output(self):
        self.fragment(["tickets"])
        self.assertEqual(
            self.run_merge()["allowedMcpServers"], [{"serverName": "tickets"}]
        )
        for line in (self.out / ".rendered.sha256").read_text().splitlines():
            digest, filename = line.split("  ", 1)
            self.assertEqual(
                digest, hashlib.sha256((self.out / filename).read_bytes()).hexdigest()
            )

    def test_allowlist_expansion_is_refused(self):
        self.fragment(
            ["tickets"], allowed=[{"serverName": "tickets"}, {"serverName": "other"}]
        )
        with self.assertRaises(ValueError):
            self.run_merge()

    def test_malformed_and_tampered_fragments_are_refused(self):
        self.fragment(["tickets"])
        (self.mcp / "managed-mcp.json").write_text("{invalid")
        with self.assertRaises(ValueError):
            self.run_merge()

    def test_valid_hash_cannot_authorise_malformed_fragment(self):
        self.fragment(["tickets"])
        raw = b"{invalid json"
        (self.mcp / "managed-mcp.json").write_bytes(raw)
        manifest = json.loads((self.mcp / "mcp-rendered.json").read_text())
        manifest["sha256"]["managed-mcp.json"] = hashlib.sha256(raw).hexdigest()
        (self.mcp / "mcp-rendered.json").write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            self.run_merge()
        self.assertFalse(self.out.exists())

    def test_hash_mismatch_never_writes_partial_output(self):
        self.fragment(["tickets"])
        (self.mcp / "managed-mcp.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            self.run_merge()
        self.assertFalse(self.out.exists())

    def test_orphan_fragment_is_refused(self):
        (self.mcp / "managed-mcp.json").write_text("{}")
        with self.assertRaises(ValueError):
            self.run_merge()

    def test_codex_fragment_cannot_override_policy(self):
        with self.assertRaises(ValueError):
            self.module.validate_fragment('forced_login_method = "api"', [])

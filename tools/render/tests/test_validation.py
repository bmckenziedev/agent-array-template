"""One isolated failure for each reference-normaliser validation rule."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aa_render import model
from .test_render import FIXTURE


def docs():
    return {
        "org": copy.deepcopy(FIXTURE["org"]),
        "teams": {"version": 1, "teams": copy.deepcopy(FIXTURE["teams"])},
        "users": {
            "version": 1,
            "users": copy.deepcopy(FIXTURE["users"]),
            "tombstones": FIXTURE["tombstones"][:],
        },
        **{
            name: {"version": 1, **copy.deepcopy(FIXTURE[name])}
            for name in ("accounts", "estates", "mcp_registry", "context_sources")
        },
    }


def set_path(document, path, value):
    parts = path.split(".")
    cursor = document
    for part in parts[:-1]:
        cursor = cursor[int(part)] if isinstance(cursor, list) else cursor[part]
    key = parts[-1]
    cursor[int(key) if isinstance(cursor, list) else key] = value


class ValidationTests(unittest.TestCase):
    def assert_invalid(self, change, expected):
        document = docs()
        change(document)
        paths = {"org": "org/org.example.yaml", **document["org"]["files"]}
        by_path = {path: document[name] for name, path in paths.items()}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def load(path):
                return by_path[path.relative_to(root).as_posix()]

            with patch.object(model, "load", load), self.assertRaisesRegex(model.OrgError, expected):
                model.load_model(root, root / "org/org.example.yaml")


CASES = [
    ("org_version", "org.version", 2, "version must be 1"),
    ("session_node", "org.nodes.0.roles", ["control-plane"], "role 'sessions'"),
    ("control_plane", "org.nodes.0.roles", ["sessions"], "no control-plane"),
    ("node_short", "org.nodes.0.short", "INVALID", "bad short"),
    ("digest", "org.images.panel.digest", "sha256:bad", "bad digest"),
    ("permission_default", "org.policy.default_permission_mode", "bad", "default_permission_mode"),
    ("tier_default", "org.sessions.default_tier", "bad", "default_tier"),
    ("team_permission", "teams.teams.0.permission_mode", "bad", "permission_mode"),
    ("team_tier", "teams.teams.0.session_tier", "bad", "session_tier"),
    ("team_class", "teams.teams.0.data_classes_allowed", ["bad"], "unknown data class"),
    ("team_vendor_class", "teams.teams.0.vendors_allowed", {"bad": []}, "unknown class"),
    ("team_vendor", "teams.teams.0.vendors_allowed.public", ["bad"], "unknown vendor"),
    ("team_server", "teams.teams.0.mcp_servers", ["bad"], "unknown mcp server"),
    ("team_server_allowed", "mcp_registry.servers.0.allowed_teams", ["nobody"], "not allowed for team"),
    ("team_source", "teams.teams.0.context_sources", ["bad"], "unknown context source"),
    ("team_pool", "teams.teams.0.pools", ["bad"], "unknown pool"),
    ("team_pool_seat", "teams.teams.0.pools", ["acct-claude-seat-ana"], "is a seat"),
    ("team_pool_sharing", "teams.teams.0.pools", ["acct-openai-api-shared"], "not shared with team"),
    ("team_lead", "teams.teams.0.leads", ["bad"], "unknown lead"),
    ("account_vendor", "accounts.accounts.0.vendor", "bad", "unknown vendor"),
    ("account_type", "accounts.accounts.0.type", "bad", "bad type"),
    ("seat_holder", "accounts.accounts.0.holder", None, "seat needs holder"),
    ("seat_max", "accounts.accounts.0.max_concurrent_sessions", None, "seat needs max"),
    ("account_team", "accounts.accounts.4.owner_team", "bad", "owner_team unknown"),
    ("account_sharing", "accounts.accounts.5.shared_with", ["bad"], "shared_with unknown"),
    ("api_secret", "accounts.accounts.4.secret", None, "api account needs secret"),
    ("user_slug", "users.users.0.slug", "INVALID", "bad slug"),
    ("namespace_length", "org.namespaces.user_prefix", "x" * 64, "namespace too long"),
    ("tombstoned", "users.tombstones", ["ana"], "tombstoned"),
    ("user_status", "users.users.0.status", "bad", "bad status"),
    ("user_team", "users.users.0.teams", ["bad"], "unknown team"),
    ("user_primary", "users.users.0.primary_team", "platform", "primary_team not in teams"),
    ("user_tier", "users.users.0.tier", "bad", "unknown tier"),
    ("user_tool", "users.users.0.tools", {"bad": {}}, "unknown tool"),
    ("user_account", "users.users.0.tools.claude.account", "bad", "unknown account"),
    ("user_seat", "users.users.0.tools.claude.account", "acct-claude-seat-bo", "not this user's seat"),
    (
        "user_seat_vendor",
        "users.users.0.tools.claude.account",
        "acct-chatgpt-seat-ana",
        "vendor does not match",
    ),
    ("home_node", "users.users.0.tools.claude.home_node", "gpu-a", "not a sessions node"),
    ("estate_team", "estates.estates.0.owner_team", "bad", "unknown owner_team"),
    ("estate_class", "estates.estates.0.data_class", "bad", "unknown data_class"),
    ("estate_tool", "estates.estates.0.snapshot_targets", ["bad"], "unknown target"),
    ("estate_policy", "estates.estates.0.snapshot_targets", ["codex"], "not allowed for confidential"),
    ("mcp_transport", "mcp_registry.servers.0.transport", "bad", "bad transport"),
    ("mcp_auth", "mcp_registry.servers.0.auth", "bad", "not supported in v1"),
    ("mcp_client", "mcp_registry.servers.0.clients", ["bad"], "unknown client"),
    ("mcp_kimi", "mcp_registry.servers.0.clients", ["kimi"], "kimi MCP is disabled"),
    ("mcp_secret_env", "mcp_registry.servers.0.env", {"API_TOKEN": "example"}, "looks like a secret"),
    ("source_delivery", "context_sources.sources.0.delivery", "bad", "bad delivery"),
    ("source_server", "context_sources.sources.1.served_by", "bad", "served_by unknown"),
    ("replicas_tier", "users.users.0.tools.claude.replicas", 2, "replicas above tier"),
    ("replicas_account", "accounts.accounts.0.max_concurrent_sessions", 0, "replicas above account max"),
]


def make_test(path, value, expected):
    def test(self):
        def change(document):
            set_path(document, path, copy.deepcopy(value))
            if path == "users.users.0.slug":
                document["teams"]["teams"][0]["leads"] = []
            if path == "org.nodes.0.roles" and expected == "role 'sessions'":
                document["org"]["nodes"][1]["roles"] = ["worker"]
            if path == "teams.teams.0.pools" and expected == "not shared with team":
                document["accounts"]["accounts"][5]["shared_with"] = []
            if path == "accounts.accounts.4.owner_team":
                document["teams"]["teams"][0]["pools"] = []
            if path == "accounts.accounts.5.shared_with":
                document["teams"]["teams"][0]["pools"] = []

        self.assert_invalid(change, expected)

    return test


for name, path, value, expected in CASES:
    setattr(ValidationTests, "test_" + name, make_test(path, value, expected))


for name in ("teams", "users", "accounts", "estates", "mcp_registry", "context_sources"):
    setattr(ValidationTests, "test_version_" + name, make_test(name + ".version", 2, "version must be 1"))


def unique_test(section, list_key, key):
    def test(self):
        def change(document):
            rows = document[section][list_key]
            rows[1][key] = rows[0][key]

        self.assert_invalid(change, "duplicate")

    return test


for section, list_key, key in (
    ("teams", "teams", "id"),
    ("users", "users", "slug"),
    ("accounts", "accounts", "id"),
    ("org", "nodes", "name"),
    ("org", "nodes", "short"),
    ("mcp_registry", "servers", "name"),
    ("context_sources", "sources", "name"),
    ("estates", "estates", "id"),
):
    setattr(ValidationTests, f"test_unique_{section}_{key}", unique_test(section, list_key, key))


def special_test(kind):
    def test(self):
        def change(document):
            server = next(s for s in document["mcp_registry"]["servers"] if s["transport"] == "http")
            if kind == "image_missing":
                del document["org"]["images"]["panel"]
            elif kind == "stdio_required":
                server["transport"] = "stdio"
                server.pop("command", None)
                server.pop("image_layer", None)
            elif kind == "stdio_deploy":
                server.update(transport="stdio", command="example", image_layer="example")
            elif kind == "http_choice":
                server.pop("deploy", None)
                server.pop("service", None)
            elif kind == "http_auth":
                server["auth"] = "none"
            elif kind == "http_namespace":
                server.pop("deploy", None)
                server["service"] = {"namespace_ref": "bad"}

        expected = {
            "image_missing": "missing panel",
            "stdio_required": "stdio needs command",
            "stdio_deploy": "stdio cannot deploy",
            "http_choice": "exactly one",
            "http_auth": "http servers need auth",
            "http_namespace": "bad namespace_ref",
        }[kind]
        self.assert_invalid(change, expected)

    return test


for kind in ("image_missing", "stdio_required", "stdio_deploy", "http_choice", "http_auth", "http_namespace"):
    setattr(ValidationTests, "test_" + kind, special_test(kind))


def reused_seat_guard(self):
    # Unique slugs and tool/vendor matching make this guard redundant for parsed YAML.
    # A repeated mapping iterator directly verifies that the defensive guard remains active.
    class RepeatedTools(dict):
        def items(self):
            rows = list(super().items())
            return iter(rows + rows)

    def change(document):
        user = document["users"]["users"][0]
        user["tools"] = RepeatedTools(user["tools"])

    self.assert_invalid(change, "used twice")


ValidationTests.test_reused_seat_guard = reused_seat_guard

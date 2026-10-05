"""Cross-component auth-helper contracts use synthetic credentials only."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.test_policy_merge import load


class AuthHelpers(unittest.TestCase):
    def test_mcp_header_shape_and_missing_token(self):
        helper = load("aa-mcp-token")
        with tempfile.TemporaryDirectory() as temporary:
            token = Path(temporary) / "token"
            token.write_text("synthetic-token")
            with patch.dict(os.environ, {"AA_MCP_TOKEN_FILE": str(token)}), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(helper.main(), 0)
            import json
            self.assertEqual(json.loads(output.getvalue()), {"Authorization": "Bearer synthetic-token"})
            token.unlink()
            with patch.dict(os.environ, {"AA_MCP_TOKEN_FILE": str(token)}), contextlib.redirect_stderr(io.StringIO()) as output:
                self.assertEqual(helper.main(), 1)
            self.assertNotIn("synthetic-token", output.getvalue())

    def test_git_helper_confines_credential_to_provider_and_username(self):
        helper = load("aa-git-credential")
        environment = {"AA_GIT_PROVIDER": "github", "AA_GIT_USERNAME": "example-login"}
        for host, username, expected in [("github.com", "example-login", True), ("evil.example.org", "example-login", False), ("github.com", "another-login", False)]:
            request = f"protocol=https\nhost={host}\nusername={username}\n\n"
            with patch.dict(os.environ, environment), patch.object(helper.sys, "argv", ["helper", "get"]), patch.object(helper.sys, "stdin", io.StringIO(request)), patch.object(helper.Path, "read_text", return_value="synthetic-token") as read, contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(helper.main(), 0)
            self.assertEqual(bool(output.getvalue()), expected)
            self.assertEqual(read.called, expected)

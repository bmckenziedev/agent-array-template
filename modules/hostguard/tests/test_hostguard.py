import contextlib
import io
import ipaddress
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from hostguard import MARKER, inspect, main, read_env, render


class GuardTests(unittest.TestCase):
    def test_scope_and_dual_stack(self):
        draft = render(["10.42.0.0/16", "fd42::/64"], ["cni0"])
        self.assertIn("ip6 saddr fd42::/64", draft)
        self.assertIn("priority -10", draft)
        for forbidden in ("flush", "delete", "hook forward", "hook output", "2379 accept"):
            self.assertNotIn(forbidden, draft)
        self.assertEqual(draft, render(["fd42::/64", "10.42.0.0/16"], ["cni0"]))

    def test_invalid_inputs_fail_closed(self):
        for cidrs in ([], ["10.42.0.1/16"], ["$(command)"]):
            with self.assertRaises(ValueError):
                render(cidrs)
        with self.assertRaises(ValueError):
            render(["10.42.0.0/16"], ['x"; accept'])

    def test_inventory_preserved_and_collision(self):
        inventory = {"nftables": [{"table": {"family": "ip", "name": "filter"}}]}
        original = json.dumps(inventory)
        self.assertFalse(inspect(inventory))
        self.assertEqual(original, json.dumps(inventory))
        inventory["nftables"].append({"table": {"family": "inet", "name": "aa_hostguard"}})
        with self.assertRaises(ValueError):
            inspect(inventory)
        inventory["nftables"][-1]["table"]["comment"] = MARKER
        self.assertTrue(inspect(inventory))

    def test_traffic_fixture(self):
        def denied(interface, source, protocol, port):
            return protocol == "tcp" and port in (22, 2379, 2380) and (
                interface == "cni0" or ipaddress.ip_address(source) in
                ipaddress.ip_network("10.42.0.0/16"))
        for port in (22, 2379, 2380):
            self.assertTrue(denied("cni0", "fd42::2", "tcp", port))
            self.assertTrue(denied("overlay0", "10.42.1.2", "tcp", port))
            self.assertFalse(denied("overlay0", "100.64.0.10", "tcp", port))
        for port in (6443, 10250, 9100, 53, 443):
            self.assertFalse(denied("cni0", "10.42.0.3", "tcp", port))
        self.assertFalse(denied("eth0", "198.51.100.2", "tcp", 22))
        self.assertFalse(denied("cni0", "10.42.0.3", "icmp", 0))

    def test_fixture_template_and_modes(self):
        root = Path(__file__).resolve().parents[1]
        fixture = json.loads((root / "tests/fixtures/org.fixture.json").read_text())
        keys = dict(fixture["keys"])
        keys["M_HOSTGUARD_POD_INTERFACES_JSON"] = '["cni0"]'
        template = (root / "hostguard.tmpl.env").read_text()
        output = re.sub(r"\{\{([A-Z][A-Z0-9_]*)\}\}", lambda m: keys[m[1]], template)
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / "hostguard.env"
            env.write_text(output)
            cidrs, interfaces = read_env(env)
            self.assertEqual(cidrs, [keys["POD_CIDR"]])
            self.assertEqual(interfaces, ["cni0"])
            with patch("hostguard.subprocess.run") as run, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--env", str(env)]), 0)
                run.assert_not_called()
                run.return_value.stdout = '{"nftables": []}'
                main(["--env", str(env), "--report"])
                self.assertEqual(run.call_count, 1)
                run.reset_mock()
                main(["--env", str(env), "--check"])
                self.assertEqual(run.call_count, 2)
                self.assertIn("--check", run.call_args.args[0])
                run.reset_mock()
                main(["--env", str(env), "--apply", "--yes"])
                self.assertEqual(run.call_count, 3)
                self.assertTrue(run.call_args.kwargs["input"].startswith("create table inet aa_hostguard\n"))
                run.reset_mock()
                run.return_value.stdout = json.dumps({"nftables": [{"table": {
                    "family": "inet", "name": "aa_hostguard", "comment": MARKER}}]})
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    main(["--env", str(env), "--apply", "--yes"])
                self.assertEqual(run.call_count, 1)

    def test_mutation_requires_confirmation(self):
        with patch("hostguard.subprocess.run") as run, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                main(["--env", "unused", "--apply"])
            run.assert_not_called()

    def test_rollback_scope(self):
        doc = (Path(__file__).resolve().parents[1] / "README.md").read_text()
        self.assertIn("nft delete table inet aa_hostguard", doc)
        self.assertNotIn("nft flush ruleset", doc)

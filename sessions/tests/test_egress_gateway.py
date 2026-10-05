"""The optional gateway loads managed exclusions and bounds TLS parsing offline."""
import importlib.machinery
import importlib.util
import ipaddress
import json
import tempfile
import unittest
from pathlib import Path


class FakeSocket:
    def __init__(self, raw):
        self.raw = raw

    def recv(self, count):
        result, self.raw = self.raw[:count], self.raw[count:]
        return result


class EgressGateway(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "kimi/image/bin/aa-kimi-egress"
        loader = importlib.machinery.SourceFileLoader("kimi_egress", str(path))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        self.module = importlib.util.module_from_spec(spec)
        loader.exec_module(self.module)

    def test_managed_cidrs_exclude_public_nodes_and_private_ranges(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "denied.json"
            path.write_text(json.dumps(["203.0.113.10/32", "10.0.0.0/8"]))
            networks = self.module.denied_networks(path)
            self.assertTrue(any(ipaddress.ip_address("203.0.113.10") in n for n in networks))
            self.assertTrue(any(ipaddress.ip_address("10.2.3.4") in n for n in networks))
            path.write_text("[]")
            with self.assertRaises(ValueError):
                self.module.denied_networks(path)

    def test_truncated_and_oversized_tls_records_fail_closed(self):
        for raw in (b"", b"\x16\x03\x03", b"\x16\x03\x03\xff\xff"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.module.hello(FakeSocket(raw))

    def test_allowlist_has_only_exact_vendor_hosts(self):
        self.assertEqual(self.module.HOSTS, {"api.kimi.ai", "auth.kimi.ai"})
        self.assertNotIn("evil.kimi.ai", self.module.HOSTS)
        self.assertNotIn("api.kimi.ai.example.org", self.module.HOSTS)

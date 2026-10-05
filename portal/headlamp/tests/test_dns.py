from pathlib import Path
import unittest

import yaml


class DNSContractTests(unittest.TestCase):
    def test_dns_uses_pod_peers_on_both_protocols(self):
        policy = yaml.safe_load((Path(__file__).resolve().parents[1] /
                                 "k8s/identity-egress.tmpl.yaml").read_text())
        dns = next(rule for rule in policy["spec"]["egress"]
                   if any(port["port"] == 53 for port in rule["ports"]))
        self.assertEqual(dns["to"], [{
            "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}},
            "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
        }])
        self.assertEqual({(port["port"], port["protocol"]) for port in dns["ports"]},
                         {(53, "UDP"), (53, "TCP")})

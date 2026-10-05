"""Synthetic trees exercise the cross-component checker independently."""
import json
from pathlib import Path
import tempfile
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_contracts as contracts


class ContractsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tree = self.root / "rendered"
        self.tree.mkdir()
        for filename in contracts.DOCKERFILES:
            path = self.root / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("FROM scratch\n")
        self.write("global/argo/project.yaml", {"apiVersion": "argoproj.io/v1alpha1", "kind": "AppProject",
                   "metadata": {"name": "test"}, "spec": {"destinations": [{"namespace": "ns", "server": "*"}],
                   "clusterResourceWhitelist": [], "namespaceResourceWhitelist": [{"group": "*", "kind": "*"}]}})
        self.write("global/argo/app.yaml", {"apiVersion": "argoproj.io/v1alpha1", "kind": "Application",
                   "metadata": {"name": "all"}, "spec": {"project": "test", "destination": {"namespace": "ns", "server": "local"},
                   "source": {"path": "rendered/global", "directory": {"recurse": True, "include": "*.yaml"}}}})

    def write(self, path, doc):
        file = self.tree / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(doc))

    def check(self):
        return contracts.check(self.tree, self.root)

    def test_exact_coverage_and_project(self):
        self.assertEqual(self.check(), [])
        self.write("users/ana/pod.yaml", {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "missing"}})
        self.assertTrue(any("covered by 0" in e for e in self.check()))
        self.write("global/ns.yaml", {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "ns"}})
        self.assertTrue(any("not permitted" in e for e in self.check()))

    def test_manifest_in_files(self):
        self.write("files/oops.yaml", {"apiVersion": "v1", "kind": "ConfigMap"})
        self.assertTrue(any("under files" in e for e in self.check()))

    def test_dns_proxy_and_monitor_ports(self):
        self.write("global/dns.yaml", {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
                   "metadata": {"name": "dns", "namespace": "ns"}, "spec": {"egress": [{
                       "to": [{"ipBlock": {"cidr": "10.0.0.1/32"}}], "ports": [{"port": 53}]}]}})
        self.write("global/role.yaml", {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role",
                   "metadata": {"name": "proxy", "namespace": "ns"}, "rules": [{"resources": ["services/proxy"], "resourceNames": ["pace"]}]})
        self.write("global/monitor.yaml", {"apiVersion": "monitoring.coreos.com/v1", "kind": "ServiceMonitor",
                   "metadata": {"name": "monitor", "namespace": "ns"}, "spec": {"selector": {}, "endpoints": [{"port": "http"}]}})
        errors = self.check()
        self.assertTrue(any("DNS egress" in e for e in errors))
        self.assertTrue(any("resourceName" in e for e in errors))
        self.assertTrue(any("ServiceMonitor port" in e for e in errors))

    def test_missing_dockerfile(self):
        (self.root / contracts.DOCKERFILES[0]).unlink()
        self.assertTrue(any("Dockerfile missing" in e for e in self.check()))

    def test_multiple_applications_covering_one_path_are_rejected(self):
        self.write("global/argo/other.yaml", {"apiVersion": "argoproj.io/v1alpha1", "kind": "Application",
                   "metadata": {"name": "other"}, "spec": {"project": "test", "source": {
                       "path": "rendered/global", "directory": {"recurse": True}}}})
        self.assertTrue(any("covered by 2" in error for error in self.check()))

    def test_non_kubernetes_groups_are_raw(self):
        self.write("global/rbac.yaml", {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {
            "name": "argocd-rbac-cm", "namespace": "ns"}, "data": {"policy.csv": "g, oidc:team-a, role:member"}})
        self.assertTrue(any("prefixed group" in error for error in self.check()))

    def test_session_missing_configmap_and_unknown_image(self):
        self.write("users/ana/sessions/pod.yaml", {"apiVersion": "v1", "kind": "Pod", "metadata": {
            "name": "session", "namespace": "ns"}, "spec": {"volumes": [{"name": "base", "configMap": {
                "name": "claude-policy"}}], "containers": [{"name": "claude", "image": "example@sha256:unknown"}]}})
        errors = self.check()
        self.assertTrue(any("ConfigMap ns/claude-policy has no producer" in error for error in errors))
        self.assertTrue(any("image absent" in error for error in errors))
        self.write("global/policy.yaml", {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {
            "name": "claude-policy", "namespace": "ns"}})
        self.assertFalse(any("ConfigMap ns/claude-policy has no producer" in error for error in self.check()))

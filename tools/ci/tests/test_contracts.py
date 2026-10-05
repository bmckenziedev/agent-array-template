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

    def test_native_config_allowlist_rejects_missing_kind_and_nested_fragments(self):
        self.write('files/ops/audit/audit-policy.yaml', {'rules': []})
        with self.assertRaises(ValueError):
            self.check()
        (self.tree / 'files/ops/audit/audit-policy.yaml').unlink()
        self.write('files/modules/wazuh/overlay/nested/dashboard.yaml', {
            'apiVersion': 'apps/v1', 'kind': 'Deployment',
            'metadata': {'name': 'wazuh-dashboard'}, 'spec': {'template': {}}})
        self.assertTrue(any('under files' in e for e in self.check()))

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

    def test_real_supervised_shapes_and_isolation_mutations(self):
        import copy
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "render"))
        from aa_render.model import load_model
        from aa_render.templates import subst
        import yaml
        root = Path(__file__).resolve().parents[3]
        model = load_model(root, root / "org/org.example.yaml")
        entities = model["entities"]["user_tool"]
        entity = next(e for e in entities if e["TOOL"] == "claude")
        template = root / "sessions/claude/sts-supervised/k8s/statefulset.per-user-tool.tmpl.yaml"
        pod = yaml.safe_load(subst(template.read_text(), {**model["keys"], **entity}, "test"))["spec"]["template"]["spec"]
        self.assertEqual(contracts.supervisor_errors(pod), [])
        def sidecar(p):
            return next(c for c in p["containers"] if c["name"] == "supervisor")
        def cli(p):
            return next(c for c in p["containers"] if c["name"] == "claude")
        def token(p):
            return next(v for v in p["volumes"] if v["name"] == "supervisor-token")["projected"]["sources"][0]["serviceAccountToken"]
        changes = [
            lambda p: p.update(shareProcessNamespace=True),
            lambda p: sidecar(p)["securityContext"].update(runAsUser=1001),
            lambda p: sidecar(p)["securityContext"]["capabilities"].update(add=["SYS_PTRACE"]),
            lambda p: sidecar(p).update(ports=[{"containerPort": 8080}]),
            lambda p: sidecar(p).update(command=["sh"]),
            lambda p: sidecar(p)["volumeMounts"].append({"name": "home", "mountPath": "/home"}),
            lambda p: next(m for m in sidecar(p)["volumeMounts"] if m["name"] == "login").pop("subPath"),
            lambda p: next(m for m in sidecar(p)["volumeMounts"] if m["name"] == "login").update(readOnly=False),
            lambda p: cli(p)["volumeMounts"].append({"name": "supervisor-run", "mountPath": "/control"}),
            lambda p: cli(p)["volumeMounts"].append({"name": "supervisor-token", "mountPath": "/token"}),
            lambda p: next(v for v in p["volumes"] if v["name"] == "aa-tmux")["emptyDir"].pop("medium"),
            lambda p: token(p).update(audience="https://kubernetes.default.svc"),
            lambda p: token(p).update(expirationSeconds=3601),
            lambda p: p["volumes"].append({"name": "console", "secret": {"secretName": "console"}}),
        ]
        for index, mutate in enumerate(changes):
            with self.subTest(case=index):
                changed = copy.deepcopy(pod)
                mutate(changed)
                self.assertTrue(contracts.supervisor_errors(changed))
        for entity in entities:
            template = next((root / "sessions" / entity["TOOL"] / "sts-supervised").rglob("statefulset.per-user-tool.tmpl.yaml"))
            pod = yaml.safe_load(subst(template.read_text(), {**model["keys"], **entity}, "test"))["spec"]["template"]["spec"]
            self.assertEqual(contracts.supervisor_errors(pod), [])

import importlib.util
from pathlib import Path
import unittest
import yaml

from tests.test_templates import fixture, subst

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('velero_check', ROOT / 'velero/check_resource_policy.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class VeleroTests(unittest.TestCase):
    def test_login_class_and_host_volumes_are_skipped(self):
        _, keys = fixture()
        policy = yaml.safe_load(subst((ROOT.parent / 'velero/k8s/login-resource-policy.tmpl.yaml').read_text(), keys))
        login = keys['LOGIN_HOST_ROOT']
        storage = keys['STORAGE_CLASS_LOGIN']
        volume = {'metadata': {'labels': {'velero.io/exclude-from-backup': 'true'}},
                  'spec': {'local': {'path': login + '/ana'}, 'storageClassName': storage}}
        checker.validate(policy, [volume], login, [login], storage)
        volume['metadata']['labels'] = {}
        with self.assertRaises(ValueError):
            checker.validate(policy, [volume], login, [login], storage)

    def test_missing_mandatory_login_exclusion_refused(self):
        _, keys = fixture()
        policy = yaml.safe_load(subst((ROOT.parent / 'velero/k8s/login-resource-policy.tmpl.yaml').read_text(), keys))
        with self.assertRaises(ValueError):
            checker.validate(policy, [], keys['LOGIN_HOST_ROOT'], [], keys['STORAGE_CLASS_LOGIN'])


class VeleroRenderGateTests(unittest.TestCase):
    def test_independent_backend_gate(self):
        import subprocess
        import sys
        import tempfile
        root = ROOT.parents[1]
        source = (root / 'org/org.example.yaml').read_text()
        model = yaml.safe_load(source)
        for value in model['files'].values():
            source = source.replace('"' + value + '"', '"' + str((root / value).resolve()).replace('\\', '/') + '"')
        with tempfile.TemporaryDirectory(prefix='velero-gate-') as temp:
            work = Path(temp)
            for backend, expected in [('restic-sftp', False), ('velero', True)]:
                org = work / 'org.yaml'
                org.write_text(source.replace('kind: restic-sftp', 'kind: ' + backend).replace('kind: "restic-sftp"', 'kind: "' + backend + '"'), encoding='utf-8')
                out = work / backend
                result = subprocess.run([sys.executable, "-B", str(root / 'tools/render/render.py'), '--org', str(org), '--out', str(out)], cwd=root, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                target = out / 'global/ops/velero/k8s/login-resource-policy.yaml'
                self.assertEqual(target.exists(), expected)
                if expected:
                    docs = list(yaml.safe_load_all((out / 'global/ops/velero/k8s/namespace.yaml').read_text()))
                    self.assertEqual(docs[0]['metadata']['annotations']['argocd.argoproj.io/sync-options'], 'Prune=false,Delete=false')
                    self.assertEqual(docs[1]['spec']['policyTypes'], ['Ingress', 'Egress'])

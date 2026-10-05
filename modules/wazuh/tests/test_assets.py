"""Exact configuration inventory and malformed fragment denial."""
import copy
import importlib.util
from pathlib import Path
import unittest
import yaml
from tests.test_boundary import ROOT, subst

spec = importlib.util.spec_from_file_location('rendered_assets', ROOT.parents[1] / 'tools/ci/rendered_assets.py')
assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assets)


class Assets(unittest.TestCase):
    def test_overlay_fragments_are_exact_and_fail_closed(self):
        for path in (ROOT / 'overlay').glob('*.tmpl.*'):
            for doc in yaml.safe_load_all(subst(path.read_text())):
                if not isinstance(doc, dict) or 'kind' not in doc:
                    continue
                target = 'files/modules/wazuh/overlay/' + path.name.replace('.tmpl', '')
                self.assertTrue(assets.is_non_manifest(target, doc), target)
                bad = copy.deepcopy(doc)
                bad['kind'] = 'Deployment' if doc['kind'] != 'Deployment' else 'Secret'
                with self.assertRaises(ValueError):
                    assets.is_non_manifest(target, bad)
                self.assertFalse(assets.is_non_manifest(target + '.unknown', doc))

    def test_secret_patch_cannot_contain_data(self):
        doc = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': 'indexer-cred'}, '$patch': 'delete', 'data': {}}
        with self.assertRaises(ValueError):
            assets.is_non_manifest('files/modules/wazuh/overlay/delete-demo-secrets.yaml', doc)

    def test_namespace_prune_protected(self):
        for path in ['overlay/namespace.tmpl.yaml', 'k8s/namespace.tmpl.yaml']:
            doc = yaml.safe_load(subst((ROOT / path).read_text()))
            self.assertEqual(doc['metadata']['annotations']['argocd.argoproj.io/sync-options'], 'Prune=false,Delete=false')

    def test_native_audit_configuration_exact_path_and_shape(self):
        doc = yaml.safe_load((ROOT.parents[1] / 'ops/audit/audit-policy.tmpl.yaml').read_text())
        self.assertTrue(assets.is_non_manifest('files/ops/audit/audit-policy.yaml', doc))
        self.assertFalse(assets.is_non_manifest('files/other/audit-policy.yaml', doc))
        bad = copy.deepcopy(doc)
        bad['metadata'] = {'name': 'unreviewed'}
        with self.assertRaises(ValueError):
            assets.is_non_manifest('files/ops/audit/audit-policy.yaml', bad)

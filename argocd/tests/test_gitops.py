import copy
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT.parent
MODEL = json.loads((ROOT / 'tests/fixtures/org.fixture.json').read_text())
KEY_RE = re.compile(r'\{\{([A-Z][A-Z0-9_]*)\}\}')


def subst(text, keys):
    def rep(match):
        if match.group(1) not in keys:
            raise KeyError('unknown placeholder ' + match.group(1))
        return keys[match.group(1)]
    return KEY_RE.sub(rep, text)


def plugin():
    spec = importlib.util.spec_from_file_location('rbac_plugin', ROOT / 'render_plugin.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GitOpsTests(unittest.TestCase):
    def test_statefulset_scale_ignores_and_sync_option_cooccur(self):
        for file in ["apps/30-llm.tmpl.yaml", "apps/modules/wazuh/70-wazuh.tmpl.yaml", "apps/users-appset.tmpl.yaml"]:
            text = (ROOT / "k8s" / file).read_text()
            self.assertIn("ignoreDifferences:", text)
            self.assertIn("RespectIgnoreDifferences=true", text)
            self.assertIn(".spec.replicas", text)

    def test_policy_member_get_and_lead_sync(self):
        output = {}
        plugin().render(MODEL, output.__setitem__)
        data = json.loads(output['global/argocd/k8s/argocd-rbac-cm.yaml'])['data']
        self.assertEqual(data['policy.default'], '')
        lines = data['policy.csv'].splitlines()
        self.assertIn('g, aa-team-payments, role:team-payments', lines)
        self.assertFalse(any('oidc:' in line for line in lines))
        self.assertIn('p, role:team-payments, applications, get, users/user-bo, allow', lines)
        self.assertFalse(any('role:team-payments, applications, sync' in line for line in lines))
        self.assertTrue(any('team-payments-lead-ana, applications, sync' in line for line in lines))
        self.assertFalse(any(', exec,' in line or ', override,' in line for line in lines))

    def test_offboarded_leads_are_not_granted(self):
        model = copy.deepcopy(MODEL)
        model['entities']['user'] = [u for u in model['entities']['user'] if u['USER_SLUG'] != 'ana']
        output = {}
        plugin().render(model, output.__setitem__)
        self.assertNotIn('lead-ana', output['global/argocd/k8s/argocd-rbac-cm.yaml'])

    def test_deterministic(self):
        first, second = {}, {}
        plugin().render(MODEL, first.__setitem__)
        plugin().render(MODEL, second.__setitem__)
        self.assertEqual(first, second)

    def test_policy_injection_is_refused(self):
        model = copy.deepcopy(MODEL)
        model['keys']['GROUP_AUDITOR'] = 'group\ng, intruder, role:admin'
        with self.assertRaises(ValueError):
            plugin().render(model, lambda *_: None)

    def test_every_template_and_application_contract(self):
        keys = dict(MODEL['keys'])
        defaults = yaml.safe_load((WORK / 'modules/k3s-baremetal/org.component.defaults.yaml').read_text())
        for key, value in defaults['defaults'].items():
            keys['M_K3S_BAREMETAL_' + key.upper()] = str(value).lower() if isinstance(value, bool) else str(value)
        count = 0
        for component in [WORK / 'cluster', ROOT, WORK / 'modules/k3s-baremetal']:
            for path in component.rglob('*.tmpl.*'):
                scope = re.search(r'\.per-([a-z-]+)\.tmpl\.', path.name)
                entities = MODEL['entities'][scope.group(1).replace('-', '_')] if scope else [{}]
                for entity in entities:
                    text = subst(path.read_text(), dict(keys, **entity))
                    count += 1
                    if path.suffix == '.json':
                        json.loads(text)
                    elif path.suffix == '.yaml':
                        for obj in yaml.safe_load_all(text):
                            if not isinstance(obj, dict):
                                continue
                            if obj.get('kind') == 'Application':
                                sources = obj['spec'].get('sources', [obj['spec'].get('source')])
                                for source in sources:
                                    self.assertTrue(source['path'].startswith('rendered/'), path)
                                    self.assertEqual(source['repoURL'], keys['ORG_GIT_REMOTE'])
                                    self.assertEqual(source['directory'], {'recurse': True, 'include': '*.yaml'})
                            if obj.get('kind') == 'ApplicationSet':
                                spec = obj['spec']
                                generator = spec['generators'][0]['git']
                                self.assertTrue(generator['directories'][0]['path'].startswith('rendered/'))
                                self.assertEqual(generator['repoURL'], keys['ORG_GIT_REMOTE'])
                                self.assertTrue(spec['template']['spec']['source']['path'].startswith('rendered/'))
                                if spec['template']['spec']['project'] == 'users':
                                    dest = spec['template']['spec']['destination']['namespace']
                                    self.assertTrue(dest.startswith(keys['USER_NS_PREFIX']))
                            if obj.get('kind') == 'StorageClass':
                                self.assertEqual(obj['reclaimPolicy'], 'Retain')
                                self.assertEqual(obj['volumeBindingMode'], 'WaitForFirstConsumer')
                            if obj.get('kind') == 'AppProject':
                                name, spec = obj['metadata']['name'], obj['spec']
                                if name == 'default':
                                    self.assertEqual(spec['sourceRepos'], [])
                                    self.assertEqual(spec['destinations'], [])
                                if name == 'users':
                                    self.assertEqual([d['namespace'] for d in spec['destinations']], [keys['USER_NS_PREFIX'] + '*'])
                                    self.assertEqual(spec['clusterResourceWhitelist'], [{'group': '', 'kind': 'Namespace'}])
        self.assertGreater(count, 30)

    def test_pinned_chart_paths_and_string_admin_switch(self):
        values = yaml.safe_load(subst((ROOT / 'helm/argocd/values.tmpl.yaml').read_text(), MODEL['keys']))
        self.assertEqual(values['configs']['cm']['admin.enabled'], 'false')
        self.assertIs(values['global']['networkPolicy']['create'], False)
        self.assertIs(values['configs']['rbac']['create'], False)
        self.assertTrue(values['controller']['clusterRoleRules']['enabled'])
        for component in ['controller', 'server', 'repoServer', 'applicationSet',
                          'notifications', 'redisSecretInit', 'dex', 'redis']:
            self.assertIn('@sha256:', values[component]['image']['tag'])
        self.assertIn('@sha256:', values['dex']['initImage']['tag'])
        secrets = yaml.safe_load((ROOT / 'secrets.required.yaml').read_text())
        self.assertEqual(secrets['secrets'][0]['required_when'], 'always')

    def test_sealed_secrets_and_backup_manifest_coverage(self):
        owners = []
        for path in (ROOT / 'k8s/apps').rglob('*.tmpl.yaml'):
            for app in yaml.safe_load_all(subst(path.read_text(), MODEL['keys'])):
                if app.get('kind') != 'Application':
                    continue
                sources = app['spec'].get('sources', [app['spec'].get('source', {})])
                owners.extend(app['metadata']['name'] for source in sources
                              if source.get('path') == 'rendered/global/platform/sealed-secrets/k8s')
        self.assertEqual(owners, [MODEL['keys']['PROJECT_NAME'] + '-10-secrets'])
        backup = yaml.safe_load(subst((ROOT / 'k8s/apps/backup/85-backup-policy.tmpl.yaml').read_text(), MODEL['keys']))
        self.assertEqual(backup['spec']['source']['path'], 'rendered/global/ops/velero/k8s')
        self.assertNotIn('automated', backup['spec']['syncPolicy'])

    def test_manual_policy_and_secret_apps(self):
        for name in ['00-platform-hardening', '02-kata', '08-cluster', '10-secrets', '40-sessions-platform']:
            app = yaml.safe_load(subst((ROOT / 'k8s/apps' / (name + '.tmpl.yaml')).read_text(), MODEL['keys']))
            self.assertNotIn('automated', app['spec']['syncPolicy'])

    def test_login_helper_and_storage_isolation(self):
        docs = list(yaml.safe_load_all(subst((WORK / 'cluster/k8s/login-storage/provisioner.tmpl.yaml').read_text(), MODEL['keys'])))
        cm = next(d for d in docs if d['kind'] == 'ConfigMap')
        helper = yaml.safe_load(cm['data']['helperPod.yaml'])
        self.assertFalse(helper['spec']['automountServiceAccountToken'])
        self.assertEqual(helper['spec']['nodeSelector'], {MODEL['keys']['LABEL_PREFIX'] + '/role-sessions': 'true'})
        self.assertIn('@sha256:', helper['spec']['containers'][0]['image'])
        deployment = next(d for d in docs if d['kind'] == 'Deployment')
        container = deployment['spec']['template']['spec']['containers'][0]
        self.assertIn('@sha256:', container['image'])
        self.assertTrue(container['securityContext']['readOnlyRootFilesystem'])

    @unittest.skipUnless(shutil.which('kubeconform'), 'kubeconform not installed')
    def test_kubeconform(self):
        with tempfile.TemporaryDirectory() as tmp:
            for path in ROOT.glob('k8s/*.tmpl.yaml'):
                out = Path(tmp) / path.name
                out.write_text(subst(path.read_text(), MODEL['keys']))
                subprocess.run(['kubeconform', '-strict', '-ignore-missing-schemas', str(out)], check=True)


if __name__ == '__main__':
    unittest.main()

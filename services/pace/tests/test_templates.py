import importlib.util
import json
import re
import shutil
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / 'tests/fixtures/org.fixture.json').read_text())


def subst(text, keys):
    def replace(match):
        key = match.group(1)
        if key not in keys:
            raise KeyError('unknown placeholder ' + key)
        return str(keys[key])
    return re.sub(r'\{\{([A-Z][A-Z0-9_]*)\}\}', replace, text)


class TemplateTests(unittest.TestCase):
    def render(self):
        documents = []
        for path in sorted((ROOT / 'k8s').glob('*.yaml')):
            scopes = FIXTURE['entities']['team'] if '.per-team.' in path.name else [{}]
            for scope in scopes:
                keys = dict(FIXTURE['keys'], C_PACE_STORAGE='1Gi', **scope)
                documents.extend(yaml.safe_load_all(subst(path.read_text(encoding='utf-8-sig'), keys)))
        return documents

    def test_every_template(self):
        docs = self.render()
        self.assertTrue(all(d['apiVersion'] and d['kind'] for d in docs))
        deploy = next(d for d in docs if d['kind'] == 'Deployment')
        self.assertEqual(deploy['spec']['strategy']['type'], 'Recreate')
        self.assertEqual(deploy['spec']['replicas'], 1)
        self.assertEqual(deploy['spec']['template']['spec']['containers'][0]['image'], FIXTURE['keys']['IMAGE_PACE'])
        self.assertIn('@sha256:', FIXTURE['keys']['IMAGE_PACE'])
        service = next(d for d in docs if d['kind'] == 'Service')
        self.assertEqual(service['spec']['selector'], deploy['spec']['selector']['matchLabels'])
        self.assertEqual(service['spec']['ports'][0]['port'], 8080)
        role = next(d for d in docs if d['kind'] == 'ClusterRole')
        self.assertEqual(role['rules'][0]['verbs'], ['create'])
        self.assertEqual(role['rules'][1]['resources'], ['namespaces'])

    def test_team_subjects_exactly_fixture(self):
        docs = self.render()
        actual = sorted(d['subjects'][0]['name'] for d in docs if d['kind'] == 'RoleBinding')
        expected = sorted(e['TEAM_OIDC_GROUP'] for e in FIXTURE['entities']['team'])
        self.assertEqual(actual, expected)
        for role in (d for d in docs if d['kind'] == 'Role'):
            self.assertEqual(role['rules'][1]['resourceNames'], ['pace:8080'])
            self.assertEqual(role['rules'][1]['resources'], ['services/proxy'])
            self.assertEqual(role['rules'][1]['verbs'], ['get'])

    def test_settings_plugin(self):
        spec = importlib.util.spec_from_file_location('pace_plugin', ROOT / 'render_plugin.py')
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        outputs = {}
        plugin.render(FIXTURE, lambda path, text: outputs.update({path: text}))
        config = json.loads(next(iter(outputs.values())))
        settings = json.loads(config['data']['pace.json'])
        self.assertEqual(settings['plans'], FIXTURE['accounts']['plans'])
        self.assertEqual(settings['default_policy'], FIXTURE['accounts']['default_policy'])
        self.assertEqual(settings['platform_writers'], [FIXTURE['keys']['NS_LLM'] + '/litellm-usage'])
        self.assertEqual(settings['audience'], FIXTURE['keys']['PROJECT_NAME'] + '-pace')
        network = json.loads(outputs['global/services/pace/k8s/network.yaml'])
        endpoints = sorted(set(json.loads(FIXTURE['keys']['APISERVER_ENDPOINT_IPS_JSON'])) | {n['overlay_ip'] for n in FIXTURE['org']['nodes'] if 'control-plane' in n['roles']})
        actual = [entry['ipBlock']['cidr'] for entry in network['spec']['egress'][0]['to']]
        self.assertEqual(actual, [ip + ('/128' if ':' in ip else '/32') for ip in endpoints])
        service = network['spec']['egress'][1]['to'][0]['ipBlock']['cidr']
        self.assertEqual(service, FIXTURE['keys']['APISERVER_SERVICE_IP'] + '/32')
        dns = network['spec']['egress'][2]['ports']
        self.assertEqual({p['protocol'] for p in dns}, {'UDP', 'TCP'})

    @unittest.skipUnless(shutil.which('kubeconform'), 'kubeconform is unavailable')
    def test_kubeconform(self):
        import subprocess
        content = yaml.safe_dump_all(self.render())
        result = subprocess.run(['kubeconform', '-strict', '-ignore-missing-schemas'],
                                input=content, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    spec = importlib.util.spec_from_file_location('tested', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NodeContractTests(unittest.TestCase):
    def setUp(self):
        self.module = load(ROOT / 'node-contract/apply-node-contract.py')
        self.data = {'node': 'node-a', 'label_prefix': 'example.org',
                     'roles': ['gpu', 'sessions'], 'runtime_classes': ['kata'],
                     'labels': {'other.org/unowned': 'ignore'}}

    def test_owned_additive_labels_and_gpu_taint(self):
        commands = self.module.commands(self.data)
        self.assertIn('example.org/role-sessions=true', commands[0])
        self.assertIn('example.org/runtime-kata=true', commands[0])
        self.assertNotIn('other.org/unowned=ignore', commands[0])
        self.assertIn('example.org/gpu=true:NoSchedule', commands[1])
        self.assertFalse(any(arg.endswith('-') for cmd in commands for arg in cmd))

    def test_kubeconfig_is_native_client_configuration(self):
        import yaml
        model = json.loads((ROOT / 'tests/fixtures/org.fixture.json').read_text())
        import re
        text = (ROOT / 'oidc/kubeconfig-oidc.tmpl.yaml').read_text()
        text = re.sub(r"\{\{([A-Z][A-Z0-9_]*)\}\}", lambda m: model['keys'][m[1]], text)
        config = yaml.safe_load(text)
        self.assertEqual(config['kind'], 'Config')
        self.assertEqual(config['apiVersion'], 'v1')
        self.assertNotIn('metadata', config)
        self.assertEqual(config['users'][0]['user']['exec']['command'], 'kubectl')
        self.assertEqual(config['clusters'][0]['cluster']['server'], model['keys']['APISERVER_URL'])

    def test_invalid_node_refused(self):
        self.data['node'] = '--all'
        with self.assertRaises(ValueError):
            self.module.commands(self.data)

    def test_fake_kubectl_dry_run_and_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            contracts = base / 'nodes/node-a/cluster/node-contract'
            contracts.mkdir(parents=True)
            (contracts / 'labels.json').write_text(json.dumps(self.data))
            log = base / 'calls.txt'
            if os.name == 'nt':
                fake = base / 'kubectl.cmd'
                fake.write_text('@echo off\necho %* >> "%AA_KUBECTL_LOG%"\n')
            else:
                fake = base / 'kubectl'
                fake.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$AA_KUBECTL_LOG"\n')
                fake.chmod(0o755)
            env = dict(os.environ, PATH=str(base) + os.pathsep + os.environ['PATH'],
                       AA_KUBECTL_LOG=str(log))
            cmd = [os.sys.executable, str(ROOT / 'node-contract/apply-node-contract.py'),
                   '--rendered', tmp]
            subprocess.run(cmd, env=env, check=True, capture_output=True)
            self.assertFalse(log.exists())
            subprocess.run(cmd + ['--yes'], env=env, check=True, capture_output=True)
            self.assertEqual(len(log.read_text().splitlines()), 2)

    def test_discovery_excludes_credentials_and_connect(self):
        module = load(ROOT / 'rbac/refresh-auditor.py')
        result = module.role([{'groupVersion': 'v1', 'resources': [
            {'name': n, 'verbs': ['get', 'list', 'watch', 'create']}
            for n in ['secrets', 'pods', 'pods/log', 'pods/exec', 'serviceaccounts/token']]}])
        resources = result['rules'][0]['resources']
        self.assertEqual(resources, ['pods', 'pods/log'])

    def test_login_plugin_denies_fallback_and_lists_only_sessions(self):
        model = json.loads((ROOT / 'tests/fixtures/org.fixture.json').read_text())
        output = {}
        load(ROOT / 'render_plugin.py').render(model, output.__setitem__)
        cm = json.loads(output['global/cluster/k8s/login-storage/config.yaml'])
        self.assertEqual(model['keys']['PROJECT_NAME'] + '-login-storage', cm['metadata']['namespace'])
        policy = json.loads(output['global/cluster/k8s/login-storage/api-egress.yaml'])
        self.assertEqual(cm['metadata']['namespace'], policy['metadata']['namespace'])
        self.assertNotEqual(model['keys']['NS_SYSTEM'], cm['metadata']['namespace'])
        paths = json.loads(cm['data']['config.json'])['nodePathMap']
        self.assertEqual(paths[0]['paths'], [])
        self.assertNotIn('gpu-a', [node['node'] for node in paths])


if __name__ == '__main__':
    unittest.main()

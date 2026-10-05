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


    def test_login_setup_precreates_private_transcript_directories(self):
        import yaml
        documents = yaml.safe_load_all((ROOT / "k8s/login-storage/provisioner.tmpl.yaml").read_text())
        setup = next(d["data"]["setup"] for d in documents if d["kind"] == "ConfigMap")
        for command in ('mkdir -p', 'chmod 0700', 'chown 1000:1000'):
            line = next(line for line in setup.splitlines() if line.startswith(command))
            self.assertIn('"$VOL_DIR/projects"', line)
            self.assertIn('"$VOL_DIR/sessions"', line)
        if os.name != "nt":
            with tempfile.TemporaryDirectory() as tmp:
                volume = Path(tmp) / "login"
                # Stub ownership change; CI is an ordinary unprivileged account.
                scripts = Path(tmp) / "bin"
                scripts.mkdir()
                stub = scripts / "chown"
                stub.write_text('#!/bin/sh\nexit 0\n')
                stub.chmod(0o755)
                subprocess.run(["sh", "-c", setup], check=True, env={
                    **os.environ, "VOL_DIR": str(volume), "PATH": str(scripts) + os.pathsep + os.environ["PATH"]})
                for path in (volume, volume / "projects", volume / "sessions"):
                    self.assertTrue(path.is_dir())
                    self.assertEqual(path.stat().st_mode & 0o777, 0o700)

    def test_prebound_login_home_plans_are_node_scoped_and_dry_run(self):
        model = json.loads((ROOT / 'tests/fixtures/org.fixture.json').read_text())
        with tempfile.TemporaryDirectory() as tmp:
            model['keys']['LOGIN_HOST_ROOT'] = str(Path(tmp) / 'logins').replace('\\', '/')
            output = {}
            load(ROOT / 'render_plugin.py').render(model, output.__setitem__)
            for node in model['entities']['node']:
                if node['NODE_IS_SESSION'] != 'true':
                    continue
                script = output['files/cluster/node-prep/' + node['NODE_NAME'] + '/prepare-login-homes.sh']
                self.assertIn('home node mismatch', script)
                self.assertIn('symlink login path refused', script)
                for entity in model['entities']['user_tool']:
                    login = model['keys']['LOGIN_HOST_ROOT'] + '/' + entity['USER_SLUG'] + '/' + entity['TOOL'] + '/' + entity['TOOL_HOME_NODE']
                    if entity['TOOL_HOME_NODE'] == node['NODE_NAME']:
                        self.assertIn(login + '/projects', script)
                        self.assertIn(login + '/sessions', script)
                    else:
                        self.assertNotIn(login, script)
                if os.name != 'nt':
                    result = subprocess.run(['bash', '-c', script], check=True, capture_output=True)
                    self.assertIn(b'1000 -g 1000 -m 0700', result.stdout)
                    self.assertFalse(Path(model['keys']['LOGIN_HOST_ROOT']).exists())
                    target = Path(tmp) / 'target'
                    target.mkdir(exist_ok=True)
                    link = Path(model['keys']['LOGIN_HOST_ROOT'])
                    link.symlink_to(target, target_is_directory=True)
                    denied = subprocess.run(['bash', '-c', script], capture_output=True)
                    self.assertNotEqual(denied.returncode, 0)
                    self.assertIn(b'symlink login path refused', denied.stderr)
                    link.unlink()

if __name__ == '__main__':
    unittest.main()

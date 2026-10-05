"""Render every component template using its canonical fixture and declared defaults."""
import copy
import importlib.util
import json
from pathlib import Path
import re
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def subst(text, keys):
    def rep(match):
        if match[1] not in keys:
            raise KeyError('unknown placeholder ' + match[1])
        return keys[match[1]]
    return re.sub(r'\{\{([A-Z][A-Z0-9_]*)\}\}', rep, text)


def fixture():
    model = json.loads((ROOT / 'tests/fixtures/org.fixture.json').read_text())
    config = yaml.safe_load((ROOT / 'org.component.defaults.yaml').read_text())
    scope = config['scope']
    settings = dict(config['defaults'])
    settings.update(model['org'].get(scope, {}).get(config['name'], {}))
    if scope == 'modules':
        settings['enabled'] = True
    if config['name'] == 'arc-ci' and settings.get('controller_digest') == 'REQUIRED':
        # Use the canonical fixture's synthetic digest only for offline controller rendering.
        settings['controller_digest'] = model['keys']['IMAGE_HERMES_CHAT'].split('@sha256:')[1]
    model['org'].setdefault(scope, {})[config['name']] = settings
    keys = dict(model['keys'])
    prefix = ('M_' if scope == 'modules' else 'C_') + config['name'].replace('-', '_').upper() + '_'
    for name, value in settings.items():
        keys[prefix + name.upper()] = str(value).lower() if isinstance(value, bool) else str(value)
    return model, keys


class TemplateTests(unittest.TestCase):
    def test_all_templates_and_plugins(self):
        model, keys = fixture()
        count = 0
        for path in sorted(ROOT.rglob('*')):
            if 'tests' in path.parts or not path.is_file() or not ('.tmpl.' in path.name or path.name.endswith('.tmpl')):
                continue
            scopes = model['entities']['node'] if '.per-node.' in path.name else [{}]
            for entity in scopes:
                output = subst(path.read_text(), dict(keys, **entity))
                self.assertNotRegex(output, r'\{\{[A-Z][A-Z0-9_]*\}\}')
                if path.suffix == '.json':
                    json.loads(output)
                if path.suffix in ('.yaml', '.yml'):
                    documents = list(yaml.safe_load_all(output))
                    if 'helm' in path.parts and 'controller' in path.parts:
                        self.assertRegex(documents[0]['image']['tag'], r'@sha256:[0-9a-f]{64}$')
                    if 'k8s' in path.parts:
                        for doc in documents:
                            self.assertIn('apiVersion', doc)
                            self.assertIn('kind', doc)
                            if doc['kind'] == 'ValidatingAdmissionPolicy':
                                self.assertIn('has(', str(doc['spec']))
                            if doc['kind'] == 'ValidatingAdmissionPolicyBinding':
                                self.assertIn('namespaceSelector', doc['spec']['matchResources'])
                            if doc['kind'] == 'Deployment':
                                self.assertEqual(doc['metadata']['namespace'], keys['NS_OPS'])
                                spec = doc['spec']['template']['spec']
                                self.assertEqual(spec['runtimeClassName'], keys['RUNTIME_CLASS_VM'])
                                self.assertIn('@sha256:', spec['containers'][0]['image'])
                count += 1
        for path in sorted(ROOT.glob('*plugin.py')):
            module_spec = importlib.util.spec_from_file_location('plugin_' + path.stem, path)
            plugin = importlib.util.module_from_spec(module_spec)
            module_spec.loader.exec_module(plugin)
            emitted = []
            plugin.render(model, lambda p, t: emitted.append((p, t)))
            self.assertTrue(emitted)
            for rel, text in emitted:
                self.assertTrue(rel.startswith('global/'))
                list(yaml.safe_load_all(text))
            disabled = copy.deepcopy(model)
            name = yaml.safe_load((ROOT / 'org.component.defaults.yaml').read_text())['name']
            disabled['org']['modules'][name]['enabled'] = False
            absent = []
            plugin.render(disabled, lambda p, t: absent.append(p))
            self.assertFalse(absent)
        self.assertGreater(count, 0)

    def test_unknown_placeholder_refused(self):
        with self.assertRaises(KeyError):
            subst('{{UNDECLARED_KEY}}', {})

"""Exercise the local JSON audit-rule fields against synthetic events."""
import json
import importlib.util
from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET
import yaml

ROOT = Path(__file__).resolve().parents[1]
MODEL = json.loads((ROOT/'tests/fixtures/org.fixture.json').read_text())


def rendered_rules():
    text = (ROOT/'audit/wazuh-k8s-audit-rules.tmpl.xml').read_text()
    text = re.sub(r'\{\{([A-Z][A-Z0-9_]*)\}\}',lambda match: MODEL['keys'][match.group(1)],text)
    return ET.fromstring(text)


def matches(rule, event):
    return all(bool(re.search(field.text,event.get(field.attrib['name'],'')))
               for field in rule.findall('field'))


class Audit(unittest.TestCase):
    def test_every_template_with_fixture(self):
        keys = dict(MODEL['keys'],M_WAZUH_PSA_VERSION='latest')
        for path in ROOT.rglob('*'):
            if '.tmpl.' not in path.name:
                continue
            text = re.sub(r'\{\{([A-Z][A-Z0-9_]*)\}\}',lambda match:keys[match.group(1)],path.read_text())
            if path.suffix in ['.yaml','.yml']:
                self.assertTrue(list(yaml.safe_load_all(text)),str(path))
            elif path.suffix=='.xml':
                ET.fromstring(text)

    def test_plugin_gating_and_audit_mount_contract(self):
        spec = importlib.util.spec_from_file_location('wazuh_plugin',ROOT/'render_plugin.py')
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        model = json.loads((ROOT/'tests/fixtures/org.fixture.json').read_text())
        model['org']['modules']['wazuh']['enabled']=False
        result={}
        plugin.render(model,result.__setitem__)
        self.assertEqual(result,{})
        model['org']['modules']['wazuh']['enabled']=True
        plugin.render(model,result.__setitem__)
        rules=json.loads(result['global/modules/wazuh/k8s/audit-rules.yaml'])
        self.assertIn('100809',rules['data']['rules.xml'])
        network=json.loads(result['global/modules/wazuh/k8s/api-endpoints.yaml'])
        self.assertEqual(len(network['spec']['egress']),2)

    def test_local_ids(self):
        root=rendered_rules()
        ids = [int(rule.attrib['id']) for rule in root.iter('rule')]
        self.assertEqual(len(ids),len(root.findall('rule')))
        self.assertEqual(len(ids),len(set(ids)))
        self.assertTrue(all(100800 <= identifier <= 100819 for identifier in ids))

    def test_exec_and_attach_in_user_namespaces(self):
        rule = rendered_rules().find("rule[@id='100809']")
        for verb in ['exec','attach']:
            self.assertTrue(matches(rule,{'objectRef.namespace':'aa-u-ana','objectRef.subresource':verb}))
        for ns,verb in [('unrelated','exec'),('aa-u-ana','log'),('aa-u-ana','portforward')]:
            self.assertFalse(matches(rule,{'objectRef.namespace':ns,'objectRef.subresource':verb}))

    def test_breakglass_group_and_ticket(self):
        rule = rendered_rules().find("rule[@id='100810']")
        self.assertTrue(matches(rule,{'user.groups':MODEL['keys']['OIDC_GROUP_BREAKGLASS'],'stage':'ResponseStarted'}))
        self.assertFalse(matches(rule,{'user.groups':'oidc:aa-team-payments','stage':'ResponseStarted'}))
        self.assertFalse(matches(rule,{'user.groups':MODEL['keys']['OIDC_GROUP_BREAKGLASS']+'-unrelated','stage':'ResponseStarted'}))
        ticket = rendered_rules().find("rule[@id='100811']")
        field = 'annotations.'+MODEL['keys']['LABEL_PREFIX']+'/breakglass-ticket'
        self.assertTrue(matches(ticket,{field:'INC-EXAMPLE','stage':'ResponseComplete'}))
        self.assertFalse(matches(ticket,{'stage':'ResponseComplete'}))


    def test_vap_audit_and_breakglass_connect(self):
        root = rendered_rules()
        rule = root.find("rule[@id='100812']")
        field = 'annotations.validation.policy.admission.k8s.io/validation_failure'
        for stage in ['ResponseStarted', 'ResponseComplete']:
            self.assertTrue(matches(rule, {field: '[{"validationActions":["Audit"]}]', 'stage': stage}))
        self.assertFalse(matches(rule, {'stage': 'ResponseComplete'}))
        connect = root.find("rule[@id='100813']")
        for sub in ['exec', 'attach', 'portforward']:
            self.assertTrue(matches(connect, {'objectRef.resource': 'pods', 'objectRef.subresource': sub, 'stage': 'ResponseStarted'}))
        self.assertFalse(matches(connect, {'objectRef.resource': 'pods', 'objectRef.subresource': 'log', 'stage': 'ResponseComplete'}))
        self.assertEqual(connect.findtext('if_sid'), '100810')


if __name__ == '__main__':
    unittest.main()

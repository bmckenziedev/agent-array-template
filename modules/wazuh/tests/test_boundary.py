"""Offline boundary regression checks; no credentials or live mutations."""
import os
import json
import re
import ipaddress
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
import yaml

ROOT = Path(__file__).resolve().parents[1]
MODEL = json.loads((ROOT/'tests/fixtures/org.fixture.json').read_text())
KEYS = MODEL['keys']
KEYS = dict(KEYS, M_WAZUH_PSA_VERSION='latest')
def subst(text):
    return re.sub(r'\{\{([A-Z][A-Z0-9_]*)\}\}', lambda m: KEYS[m.group(1)], text)
DOCS = list(yaml.safe_load_all(subst((ROOT/'k8s/wazuh-agent-daemonset.tmpl.yaml').read_text())))
CM, DS = DOCS
POLICIES = list(yaml.safe_load_all(subst((ROOT/'k8s/networkpolicy.tmpl.yaml').read_text())))
BASH = shutil.which('bash')
if os.name == 'nt':
    BASH = r'C:\Program Files\Git\bin\bash.exe'

def render(path):
    return list(yaml.safe_load_all(subprocess.check_output(['kubectl', 'kustomize', str(path)], text=True)))

def selected(selector, labels):
    return all(labels.get(k) == v for k, v in selector.get('matchLabels', {}).items())

def allowed(app, direction, other_app, port, protocol='TCP', namespace='agent-array-wazuh', ip=None):
    # All policies use matchLabels; namespace AND pod selectors in one peer.
    applicable = [p['spec'] for p in POLICIES if selected(p['spec']['podSelector'], {'app': app}) and direction in p['spec']['policyTypes']]
    if not applicable:
        return True
    key, peers = ('ingress', 'from') if direction == 'Ingress' else ('egress', 'to')
    for policy in applicable:
        for rule in policy.get(key, []):
            if not any(p['port'] == port and p.get('protocol', 'TCP') == protocol for p in rule['ports']):
                continue
            for peer in rule.get(peers, []):
                if 'ipBlock' in peer:
                    block = peer['ipBlock']
                    if ip and ipaddress.ip_address(ip) in ipaddress.ip_network(block['cidr']) and not any(ipaddress.ip_address(ip) in ipaddress.ip_network(x) for x in block.get('except', [])):
                        return True
                    continue
                ns = peer.get('namespaceSelector')
                if ns is None and namespace != 'agent-array-wazuh':
                    continue
                if ns is not None and not selected(ns, {'kubernetes.io/metadata.name': namespace}):
                    continue
                if selected(peer.get('podSelector', {}), {'app': other_app, 'k8s-app': other_app}):
                    return True
    return False

class Boundary(unittest.TestCase):
    def test_mount_boundary(self):
        spec = DS['spec']['template']['spec']
        paths = {v['hostPath']['path'] for v in spec['volumes'] if 'hostPath' in v}
        self.assertEqual(paths, {'/usr/bin', '/usr/sbin', '/var/lib/agent-array/wazuh-agent', *('/var/log/'+n+'.log' for n in ('auth','kern','ufw','dpkg'))})
        for forbidden in ('/root/.ssh', '/etc/rancher/k3s/k3s.yaml', '/root/.agent-array', '/var/lib/rancher/k3s', '/var/lib/agent-array/logins', '/var/lib/agent-array/hermes', '/var/lib/agent-array/backup', '/opt/agent-array'):
            self.assertFalse(any(forbidden == p or forbidden.startswith(p.rstrip('/')+'/') for p in paths), forbidden)
        self.assertFalse(spec['hostNetwork'])
        self.assertFalse(spec.get('hostPID', False))
        self.assertFalse(spec.get('hostIPC', False))
        self.assertFalse(spec['automountServiceAccountToken'])
        self.assertEqual(spec['securityContext']['seccompProfile']['type'], 'RuntimeDefault')
        c = spec['containers'][0]
        self.assertFalse(c['securityContext']['allowPrivilegeEscalation'])
        self.assertFalse({'DAC_OVERRIDE','DAC_READ_SEARCH','FSETID'} & set(c['securityContext']['capabilities']['add']))
        self.assertEqual(c['securityContext']['capabilities']['drop'], ['ALL'])
        self.assertTrue(all(v.get('readOnly') for v in c['volumeMounts'] if v['name'].startswith('host-')))

    def test_local_entrypoint(self):
        # Plain mock text, no login/enrollment values or identity files.
        with tempfile.TemporaryDirectory(prefix='wazuh-boundary-') as temp:
            work = Path(temp)
            def posix(p):
                return str(p).replace('\\','/').replace('C:', '/c') if os.name == 'nt' else str(p)
            conf = work/'mock.xml'
            opts = work/'mock.options'
            policy = work/'mock-policy.xml'
            policy.write_text(CM['data']['node.conf'])
            conf.write_text('<ossec_config><client><server><address>mock-manager</address></server></client><syscheck><directories>/host/root/etc</directories></syscheck><active-response><disabled>no</disabled></active-response><wodle name="command"><command>mock</command></wodle><localfile><log_format>command</log_format><command>mock</command></localfile></ossec_config>')
            opts.write_text('agent.remote_conf=1\nagent.remote_conf = 1\nwazuh_command.remote_commands=1\nlogcollector.remote_commands=1\nsca.remote_commands=1\nother.option=7\n')
            script = CM['data']['10-container-scope.sh'].replace('/var/ossec/etc/ossec.conf', posix(conf)).replace('/var/ossec/etc/local_internal_options.conf', posix(opts)).replace('/local-policy/node.conf', posix(policy))
            for _ in range(2):
                subprocess.run([BASH, '-c', script], check=True, capture_output=True, text=True)
                root = ET.fromstring(conf.read_text())
                self.assertEqual(root.findtext('client/server/address'), 'mock-manager')
                self.assertEqual(root.findtext('active-response/disabled'), 'yes')
                self.assertEqual(root.findtext('rootcheck/disabled'), 'yes')
                self.assertEqual(root.findtext('sca/enabled'), 'no')
                self.assertIsNone(root.find("wodle[@name='command']"))
                self.assertTrue(all(x.find('command') is None for x in root.findall('localfile')))
                self.assertEqual(root.find('syscheck/directories').attrib['report_changes'], 'no')
                self.assertNotIn('/host/root/etc', conf.read_text())
                for key in ('agent.remote_conf', 'wazuh_command.remote_commands', 'logcollector.remote_commands', 'sca.remote_commands'):
                    self.assertEqual([x for x in opts.read_text().splitlines() if x.startswith(key)], [key+'=0'])
            conf.write_text('<ossec_config/>')
            self.assertNotEqual(subprocess.run([BASH, '-c', script], capture_output=True).returncode, 0)

    def test_network_positive(self):
        for source, target, ports in [('wazuh-agent','wazuh-manager',[1514,1515]), ('wazuh-manager','wazuh-indexer',[9200]), ('wazuh-dashboard','wazuh-indexer',[9200]), ('wazuh-dashboard','wazuh-manager',[55000])]:
            for port in ports:
                self.assertTrue(allowed(source,'Egress',target,port))
                self.assertTrue(allowed(target,'Ingress',source,port))
        for app in ('wazuh-agent','wazuh-manager','wazuh-indexer','wazuh-dashboard'):
            for protocol in ('TCP','UDP'):
                self.assertTrue(allowed(app,'Egress','kube-dns',53,protocol,'kube-system'))

    def test_network_negative(self):
        intended = {('wazuh-agent','wazuh-manager',1514), ('wazuh-agent','wazuh-manager',1515), ('wazuh-manager','wazuh-indexer',9200), ('wazuh-dashboard','wazuh-indexer',9200), ('wazuh-dashboard','wazuh-manager',55000)}
        apps = ('wazuh-agent','wazuh-manager','wazuh-indexer','wazuh-dashboard','unrelated')
        for source in apps:
            for target in apps:
                for port in (22,53,443,1514,1515,1516,5601,9200,9300,55000,6443):
                    if (source,target,port) not in intended:
                        self.assertFalse(allowed(source,'Egress',target,port) and allowed(target,'Ingress',source,port), (source,target,port))
        for app in apps:
            for ns in ('default','aa-u-ana','monitoring'):
                self.assertFalse(allowed(app,'Ingress','wazuh-dashboard',9200,namespace=ns))
                self.assertFalse(allowed(app,'Egress','wazuh-indexer',9200,namespace=ns))
            self.assertFalse(allowed(app,'Egress','external',443,namespace='external'))
        self.assertFalse(allowed('wazuh-agent','Egress','kube-dns',53,'UDP','default'))
        self.assertFalse(allowed('wazuh-agent','Egress','unrelated',53,'UDP','kube-system'))
        self.assertFalse(allowed('wazuh-manager','Ingress','wazuh-agent',1514,'UDP'))

    def test_standalone_psa_and_pins(self):
        namespace = yaml.safe_load(subst((ROOT/'k8s/namespace.tmpl.yaml').read_text()))
        labels = namespace['metadata']['labels']
        self.assertEqual(labels['pod-security.kubernetes.io/enforce'], 'privileged')
        self.assertEqual(labels['pod-security.kubernetes.io/audit'], 'restricted')
        self.assertEqual(labels['pod-security.kubernetes.io/warn'], 'restricted')
        self.assertEqual(len(POLICIES), 6)
        self.assertNotIn('20-persist-keys.sh',CM['data'])
        self.assertIn('@sha256:',DS['spec']['template']['spec']['containers'][0]['image'])

    def test_api_endpoint_exception(self):
        for app in ('wazuh-agent', 'wazuh-manager', 'wazuh-indexer', 'wazuh-dashboard', 'unrelated'):
            self.assertTrue(allowed(app, 'Egress', 'apiserver', 6443, namespace='external', ip='100.64.0.10'))
            self.assertFalse(allowed(app, 'Egress', 'apiserver', 443, namespace='external', ip='100.64.0.10'))
            self.assertFalse(allowed(app, 'Egress', 'apiserver', 6443, namespace='external', ip='100.64.0.20'))

if __name__ == '__main__':
    unittest.main(verbosity=2)

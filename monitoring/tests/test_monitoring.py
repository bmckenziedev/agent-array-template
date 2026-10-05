"""Organization routing, templates, dashboards and effective RBAC regression tests."""
import copy
import importlib.util
import json
from pathlib import Path
import re
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PLUGIN = load('monitoring_plugin', ROOT / 'render_plugin.py')
POST = load('monitoring_post', ROOT / 'kube-prometheus-stack/post_render.py')
VALIDATE = load('monitoring_validate', ROOT / 'alerts/validate.py')


class Routing(unittest.TestCase):
    def setUp(self):
        self.model = json.loads((ROOT / 'tests/fixtures/org.fixture.json').read_text())

    def test_fixture_is_deterministic_and_secret_free(self):
        first, second = {}, {}
        PLUGIN.render(self.model, first.__setitem__)
        PLUGIN.render(self.model, second.__setitem__)
        self.assertEqual(first, second)
        for path, text in first.items():
            if path.endswith('.yaml'):
                for document in yaml.safe_load_all(text):
                    self.assertNotEqual(document.get('kind'), 'Secret')
            self.assertNotRegex(text, r'https://(?:ntfy|hc-ping)')
            self.assertNotRegex(text, r'\{\{[A-Z][A-Z0-9_]*\}\}')
        for receiver in json.loads(first['files/monitoring/helm/kube-prometheus-stack/routing.values.yaml'])['alertmanager']['config']['receivers']:
            for configs in receiver.values():
                if isinstance(configs, list):
                    for config in configs:
                        self.assertFalse({'url', 'api_url', 'routing_key', 'auth_password'} & config.keys())

    def test_receiver_kinds(self):
        expected = {'webhook': ('webhook_configs','url_file'), 'ntfy': ('webhook_configs','url_file'),
                    'slack': ('slack_configs','api_url_file'), 'pagerduty': ('pagerduty_configs','routing_key_file'),
                    'email': ('email_configs','auth_password_file')}
        for kind, (configs, field) in expected.items():
            with self.subTest(kind=kind):
                entry = dict(name='platform', kind=kind, secret='receiver-secret', key='credential',
                             to='ops@example.org', **{'from':'alerts@example.org'},
                             smarthost='smtp.example.org:587', auth_username='alerts',
                             url='DO-NOT-COPY', api_key='DO-NOT-COPY')
                result = PLUGIN.receiver(entry)
                self.assertEqual(result[configs][0][field], '/etc/alertmanager/secrets/receiver-secret/credential')
                self.assertNotIn('DO-NOT-COPY', json.dumps(result))

    def test_invalid_receiver_references(self):
        for kind, secret, key in [('bad','valid','key'),('webhook','../escape','key'),('slack','valid','../key')]:
            with self.assertRaises(ValueError):
                PLUGIN.receiver(dict(name='platform', kind=kind, secret=secret, key=key))

    def test_email_requires_metadata(self):
        with self.assertRaises(ValueError):
            PLUGIN.receiver(dict(name='email', kind='email', secret='email', key='password'))

    def test_overnight_quiet_hours(self):
        config, secrets = PLUGIN.build_config(self.model)
        intervals = config['time_intervals'][0]['time_intervals'][0]
        self.assertEqual(intervals['location'], 'UTC')
        self.assertEqual(intervals['times'], [dict(start_time='22:00',end_time='24:00'),
                                             dict(start_time='00:00',end_time='07:00')])
        self.assertEqual(config['route']['routes'][1]['group_wait'], '0s')
        self.assertEqual(config['route']['routes'][-1]['mute_time_intervals'], ['quiet-hours'])

    def test_same_day_quiet_hours_and_invalid_time(self):
        self.assertEqual(len(PLUGIN.quiet_intervals('12:00','13:00','UTC')[0]['time_intervals'][0]['times']), 1)
        for start,end in [('24:00','07:00'),('7:00','08:00'),('07:00','07:00')]:
            with self.assertRaises(ValueError):
                PLUGIN.quiet_intervals(start,end,'UTC')

    def test_team_routes_apply_severity_and_quiet_hours(self):
        self.model['org']['components']['monitoring'] = {'team_receivers': {'payments':'platform-oncall'}}
        config, _ = PLUGIN.build_config(self.model)
        team = config['route']['routes'][1]
        self.assertEqual(team['matchers'], ['team = "payments"'])
        self.assertEqual(team['routes'][0]['group_wait'], '0s')
        self.assertEqual(team['routes'][-1]['mute_time_intervals'], ['quiet-hours'])

    def test_invalid_team_routes_fail(self):
        self.model['org']['components']['monitoring'] = {'team_receivers': {'unknown':'platform-oncall'}}
        with self.assertRaises(ValueError):
            PLUGIN.build_config(self.model)

    def test_disabled_heartbeat(self):
        self.model['org']['alerting']['heartbeat']['enabled'] = False
        config, _ = PLUGIN.build_config(self.model)
        self.assertNotIn('heartbeat', {r['name'] for r in config['receivers']})
        self.assertEqual(config['route']['routes'][0]['receiver'], 'null')

    def test_tenant_safe_inhibition(self):
        config, _ = PLUGIN.build_config(self.model)
        self.assertIn('team', config['inhibit_rules'][0]['equal'])
        rule = config['inhibit_rules'][-1]
        self.assertIn('node =~ ".+"', rule['source_matchers'])
        self.assertIn('node =~ ".+"', rule['target_matchers'])

    def test_oidc_roles(self):
        outputs = {}
        PLUGIN.render(self.model, outputs.__setitem__)
        fragment = json.loads(outputs['files/monitoring/helm/kube-prometheus-stack/routing.values.yaml'])
        role = fragment['grafana']['grafana.ini']['auth.generic_oauth']['role_attribute_path']
        self.assertIn(self.model['keys']['GROUP_PLATFORM_ADMIN'], role)
        self.assertIn(self.model['keys']['GROUP_AUDITOR'], role)
        for team in self.model['teams']:
            self.assertIn(team['idp_group'], role)
        self.assertNotIn('Editor', role)

    def test_custom_label_prefix_and_module_gates(self):
        self.model['keys']['LABEL_PREFIX'] = 'organization.example.net'
        self.model['org']['modules']['gpu-lanes']['enabled'] = True
        outputs = {}
        PLUGIN.render(self.model, outputs.__setitem__)
        rule = outputs['global/monitoring/k8s/gpu.rules.yaml']
        self.assertIn('label_organization_example_net_role_gpu', rule)
        self.model['org']['modules']['gpu-lanes']['enabled'] = False
        outputs = {}
        PLUGIN.render(self.model, outputs.__setitem__)
        self.assertNotIn('global/monitoring/k8s/gpu.rules.yaml', outputs)


class Templates(unittest.TestCase):
    def test_all_templates_with_fixture(self):
        keys = VALIDATE.fixture_keys()
        model = json.loads((ROOT / 'tests/fixtures/org.fixture.json').read_text())
        for path in ROOT.rglob('*.tmpl.yaml'):
            scopes = model['entities']['mcp'] if '.per-mcp.' in path.name else [{}]
            for scope in scopes:
                text = PLUGIN.subst(path.read_text(), dict(keys, **scope))
                docs = list(yaml.safe_load_all(text))
                self.assertTrue(docs, str(path))
                if '/k8s/' in path.as_posix():
                    for doc in docs:
                        self.assertIn('apiVersion', doc, str(path))
                        self.assertIn('kind', doc, str(path))
                        self.assertIn('app.kubernetes.io/part-of', doc['metadata']['labels'], str(path))

    def test_services_match_component_contract(self):
        keys = VALIDATE.fixture_keys()
        for path in (ROOT/'k8s/monitors').glob('*.tmpl.yaml'):
            doc = yaml.safe_load(PLUGIN.subst(path.read_text(),keys))
            self.assertEqual(doc['metadata']['namespace'],keys['NS_MONITORING'])
            if doc['kind']=='ServiceMonitor':
                self.assertIn('app.kubernetes.io/name',doc['spec']['selector']['matchLabels'])
                self.assertIn(doc['spec']['endpoints'][0]['port'],['http','metrics'])

    def test_complete_values_invariants(self):
        values = yaml.safe_load(PLUGIN.subst((ROOT/'helm/kube-prometheus-stack/values.tmpl.yaml').read_text(),VALIDATE.fixture_keys()))
        self.assertNotIn('secrets',values['kube-state-metrics']['collectors'])
        self.assertFalse(values['prometheus']['prometheusSpec']['enableAdminAPI'])
        self.assertTrue(values['grafana']['rbac']['namespaced'])
        self.assertEqual(values['grafana']['admin']['existingSecret'],'grafana-admin')
        self.assertTrue(values['grafana']['grafana.ini']['auth.generic_oauth']['use_pkce'])
        self.assertIn('role-*',values['kube-state-metrics']['metricLabelsAllowlist'][0])

    def test_dashboard_json_and_node_variables(self):
        for path in (ROOT/'dashboards').glob('*.json'):
            doc = json.loads(path.read_text())
            self.assertIn('node', {v['name'] for v in doc['templating']['list']},str(path))
            self.assertNotRegex(path.read_text(),r'\b(?:node-a|node-b|gpu-a|gpu-b)\b')


class PostRenderer(unittest.TestCase):
    def raw(self):
        ns = 'example-monitoring'
        def resource(kind, name, **fields):
            return dict(apiVersion=POST.API,kind=kind,metadata=dict(name=name,namespace=ns,labels={}),**fields)
        cluster = resource('ClusterRole','kps-operator',rules=[
            POST.rule([''],['nodes'],['get','list','watch']),
            POST.rule([''],['namespaces'],['get','list','watch']),
            POST.rule(['storage.k8s.io'],['storageclasses'],['get','list','watch']),
            POST.rule([''],['secrets'],['get','list','watch','create','update','delete']),
            POST.rule(['apps'],['statefulsets'],['get','list','watch','create','update','delete']),
            POST.rule(['monitoring.coreos.com'],['servicemonitors','prometheusrules'],['get','list','watch']),
        ])
        cluster['metadata']['labels']['chart'] = 'kube-prometheus-stack-91.8.2'
        args = [f'--{key}-namespaces={ns}' for key in ['prometheus-instance','alertmanager-instance',
                'alertmanager-config','thanos-ruler-instance']]
        args += [f'--namespaces={ns}','--watch-referenced-objects-in-all-namespaces=false','--kubelet-service=kube-system/kps-kubelet']
        deployment = resource('Deployment','kps-operator',spec=dict(template=dict(spec=dict(containers=[dict(args=args)]))))
        binding = resource('ClusterRoleBinding','kps-operator',roleRef=dict(kind='ClusterRole',name='kps-operator'),
                           subjects=[dict(kind='ServiceAccount',name='kps-operator',namespace=ns)])
        grafana = resource('Role','kps-grafana',rules=[POST.rule([''],['secrets','configmaps'],['get','list','watch'])])
        ksm = resource('ClusterRole','kps-kube-state-metrics',rules=[POST.rule([''],['pods'],['list','watch'])])
        return [cluster,deployment,binding,grafana,ksm]

    def test_effective_permission_boundary(self):
        docs = POST.transform(self.raw())
        roles = [doc for doc in docs if doc['kind'] in ['Role','ClusterRole']]
        for role in roles:
            for rule in role['rules']:
                if 'secrets' in rule['resources']:
                    self.assertEqual(role['kind'],'Role')
                    self.assertEqual(role['metadata']['namespace'],'example-monitoring')
                    self.assertEqual(role['metadata']['name'],'kps-operator')
                self.assertFalse({'pods/exec','roles','rolebindings','pods','deployments'} & set(rule['resources'])
                                 if role['metadata']['name'] == 'kps-operator' else False)
        grafana = next(r for r in roles if r['metadata']['name']=='kps-grafana')
        self.assertEqual(grafana['rules'],[POST.rule([''],['configmaps'],['get','list','watch'])])
        cluster = next(r for r in roles if r['kind']=='ClusterRole' and r['metadata']['name']=='kps-operator')
        self.assertEqual({r for rule in cluster['rules'] for r in rule['resources']}, {'nodes','namespaces','storageclasses'})

    def test_fail_closed_chart_drift(self):
        docs = self.raw()
        docs[0]['metadata']['labels']['chart'] = 'different'
        with self.assertRaises(ValueError):
            POST.transform(docs)

    def test_fail_closed_secret_collector(self):
        docs = self.raw()
        docs[-1]['rules'].append(POST.rule([''],['secrets'],['list']))
        with self.assertRaises(ValueError):
            POST.transform(docs)

    def test_fail_closed_scope_drift(self):
        docs = self.raw()
        docs[1]['spec']['template']['spec']['containers'][0]['args'][0] = '--prometheus-instance-namespaces=other'
        with self.assertRaises(ValueError):
            POST.transform(docs)


if __name__ == '__main__':
    unittest.main()

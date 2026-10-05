"""Render pace settings separately from the shared org directory."""
import json

PLUGIN_NAME = 'pace-settings'


def render(model, emit):
    keys = model['keys']
    settings = {
        'audience': keys['PROJECT_NAME'] + '-pace',
        'label_prefix': keys['LABEL_PREFIX'],
        'user_ns_prefix': keys['USER_NS_PREFIX'],
        'default_policy': model['accounts']['default_policy'],
        'plans': model['accounts']['plans'],
        'platform_writers': [writer.replace('{{NS_LLM}}', keys['NS_LLM']) for writer in
            model['org'].get('components', {}).get('pace', {}).get('platform_writers', [keys['NS_LLM'] + '/litellm-usage'])],
    }
    manifest = {
        'apiVersion': 'v1', 'kind': 'ConfigMap',
        'metadata': {'name': 'pace-config', 'namespace': keys['NS_SYSTEM'],
                     'labels': {'app.kubernetes.io/name': 'pace',
                                'app.kubernetes.io/part-of': keys['PROJECT_NAME'],
                                'app.kubernetes.io/component': 'pacing'}},
        'data': {'pace.json': json.dumps(settings, sort_keys=True, separators=(',', ':'))},
    }
    emit('global/services/pace/k8s/config.yaml', json.dumps(manifest, sort_keys=True, indent=2) + '\n')

    endpoints = sorted(set(json.loads(keys['APISERVER_ENDPOINT_IPS_JSON'])) |
                       {n['overlay_ip'] for n in model['org']['nodes'] if 'control-plane' in n['roles']})
    def block(ip):
        return {'ipBlock': {'cidr': ip + ('/128' if ':' in ip else '/32')}}

    def namespace(name):
        return {'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': name}}}

    farm = namespace(keys['NS_SYSTEM'])
    farm['podSelector'] = {'matchLabels': {'app.kubernetes.io/name': 'farm-mcp'}}
    sources = [
        {'namespaceSelector': {'matchLabels': {keys['LABEL_PREFIX'] + '/kind': 'user-sessions'}}},
        farm, namespace(keys['NS_FACTORY']), namespace(keys['NS_MONITORING']), namespace(keys['NS_LLM']),
    ] + [block(ip) for ip in endpoints]
    policy = {
        'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy',
        'metadata': {'name': 'pace', 'namespace': keys['NS_SYSTEM'],
                     'labels': {'app.kubernetes.io/name': 'pace',
                                'app.kubernetes.io/part-of': keys['PROJECT_NAME'],
                                'app.kubernetes.io/component': 'pacing'}},
        'spec': {
            'podSelector': {'matchLabels': {'app.kubernetes.io/name': 'pace'}},
            'policyTypes': ['Ingress', 'Egress'],
            'ingress': [{'from': sources, 'ports': [{'port': 8080, 'protocol': 'TCP'}]}],
            # Endpoint rules cover post-DNAT CNIs; service rules cover pre-DNAT CNIs.
            'egress': [
                {'to': [block(ip) for ip in endpoints],
                 'ports': [{'port': int(keys['APISERVER_PORT']), 'protocol': 'TCP'}]},
                {'to': [block(keys['APISERVER_SERVICE_IP'])],
                 'ports': [{'port': 443, 'protocol': 'TCP'}]},
                {'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'kube-system'}},
                         'podSelector': {'matchLabels': {'k8s-app': 'kube-dns'}}}],
                 'ports': [{'port': 53, 'protocol': 'UDP'}, {'port': 53, 'protocol': 'TCP'}]},
            ],
        },
    }
    emit('global/services/pace/k8s/network.yaml', json.dumps(policy, sort_keys=True, indent=2) + '\n')

"""Emit chat configuration into the workload ConfigMap, without secret values."""
import json
from pathlib import Path
import re

PLUGIN_NAME = 'hermes-ops-chat-config'


def render(model, emit):
    cfg = model['org'].get('modules', {}).get('hermes-ops-chat', {})
    if not cfg.get('enabled', False):
        return
    keys = dict(model['keys'])
    defaults = {'platform': 'telegram', 'allowed_team': 'platform', 'model': 'openai-api-standard'}
    defaults.update(cfg)
    for name, value in defaults.items():
        keys['M_HERMES_OPS_CHAT_' + name.upper()] = str(value)
    template = (Path(__file__).parent / 'config.tmpl.json').read_text()
    text = re.sub(r'\{\{([A-Z][A-Z0-9_]*)\}\}', lambda m: keys[m[1]], template)
    content = {'apiVersion': 'v1', 'kind': 'ConfigMap',
        'metadata': {'name': 'hermes-ops-chat-config', 'namespace': keys['NS_OPS']},
        'data': {'config.json': json.dumps(json.loads(text), sort_keys=True)}}
    emit('global/modules/hermes-ops-chat/k8s/config.yaml', json.dumps(content, sort_keys=True) + '\n')
    directory = {'apiVersion': 'v1', 'kind': 'ConfigMap',
        'metadata': {'name': 'org-directory', 'namespace': keys['NS_OPS']},
        'data': {'users.json': json.dumps(model['users'], sort_keys=True),
                 'teams.json': json.dumps(model['teams'], sort_keys=True)}}
    emit('global/modules/hermes-ops-chat/k8s/org-directory.yaml', json.dumps(directory, sort_keys=True) + '\n')
    render_network(model, emit)


def render_network(model, emit):
    if not model['org'].get('modules', {}).get('hermes-ops-chat', {}).get('enabled', False):
        return
    keys = model['keys']
    for namespace in [keys['NS_OPS']]:
        # kube-router evaluates after DNAT; admit both service and endpoint addresses.
        ips = json.loads(keys['APISERVER_ENDPOINT_IPS_JSON']) + [keys['APISERVER_SERVICE_IP']]
        peers = [{'ipBlock': {'cidr': ip + ('/128' if ':' in ip else '/32')}} for ip in sorted(set(ips))]
        egress = [{'to': peers, 'ports': [{'protocol': 'TCP', 'port': int(keys['APISERVER_PORT'])}]}]
        egress.append({'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'kube-system'}},
                              'podSelector': {'matchLabels': {'k8s-app': 'kube-dns'}}}],
                       'ports': [{'protocol': p, 'port': 53} for p in ['UDP', 'TCP']]})
        egress.append({'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': keys['NS_LLM']}},
                              'podSelector': {'matchLabels': {'app.kubernetes.io/name': 'litellm'}}}],
                       'ports': [{'protocol': 'TCP', 'port': 4000}]})
        egress.append({'to': [{'namespaceSelector': {'matchLabels': {
                           'kubernetes.io/metadata.name': keys['NS_MONITORING']}}}],
                       'ports': [{'protocol': 'TCP', 'port': p} for p in [9090, 9093]]})
        egress.append({'to': [{'ipBlock': {'cidr': '0.0.0.0/0',
                           'except': json.loads(keys['SESSION_EGRESS_DENY_CIDRS_JSON'])}}],
                       'ports': [{'protocol': 'TCP', 'port': 443}]})
        policy = {'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy',
                  'metadata': {'name': 'component-egress', 'namespace': namespace},
                  'spec': {'podSelector': {'matchLabels': {'app.kubernetes.io/name': 'hermes-ops-chat'}}, 'policyTypes': ['Egress'], 'egress': egress}}
        emit('global/modules/hermes-ops-chat/k8s/egress-' + namespace + '.yaml', json.dumps(policy, sort_keys=True) + '\n')

"""Emit endpoint-specific network rules; no network calls occur during rendering."""
import json

PLUGIN_NAME = 'modules/arc-ci-network'


def render(model, emit):
    if not model['org'].get('modules', {}).get('arc-ci', {}).get('enabled', False):
        return
    keys = model['keys']
    for namespace in [keys['PROJECT_NAME'] + '-arc-systems']:
        # kube-router evaluates after DNAT; admit both service and endpoint addresses.
        ips = json.loads(keys['APISERVER_ENDPOINT_IPS_JSON']) + [keys['APISERVER_SERVICE_IP']]
        peers = [{'ipBlock': {'cidr': ip + ('/128' if ':' in ip else '/32')}} for ip in sorted(set(ips))]
        egress = [{'to': peers, 'ports': [{'protocol': 'TCP', 'port': int(keys['APISERVER_PORT'])}]}]
        egress.append({'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'kube-system'}},
                              'podSelector': {'matchLabels': {'k8s-app': 'kube-dns'}}}],
                       'ports': [{'protocol': p, 'port': 53} for p in ['UDP', 'TCP']]})
        egress.append({'to': [{'ipBlock': {'cidr': '0.0.0.0/0',
                           'except': json.loads(keys['SESSION_EGRESS_DENY_CIDRS_JSON'])}}],
                       'ports': [{'protocol': 'TCP', 'port': 443}]})
        policy = {'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy',
                  'metadata': {'name': 'component-egress', 'namespace': namespace},
                  'spec': {'podSelector': {}, 'policyTypes': ['Egress'], 'egress': egress}}
        emit('global/modules/arc-ci/k8s/egress-' + namespace + '.yaml', json.dumps(policy, sort_keys=True) + '\n')

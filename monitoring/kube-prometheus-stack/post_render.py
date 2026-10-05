#!/usr/bin/env python3
"""Helm post-renderer for chart 91.8.2, release kps, namespace monitoring.

Fail closed on unexpected input. Never print manifests/errors containing input.
Requires PyYAML 6.0.3. No live API calls, disk fixtures or Secret inspection.
"""
import copy
import sys
import yaml

API = 'rbac.authorization.k8s.io/v1'


def rule(groups, resources, verbs, names=None):
    result = dict(apiGroups=groups, resources=resources, verbs=verbs)
    if names:
        result['resourceNames'] = names
    return result


def binding(name, namespace, role, release_namespace):
    return dict(apiVersion=API, kind='RoleBinding',
                metadata=dict(name=name, namespace=namespace),
                roleRef=dict(apiGroup='rbac.authorization.k8s.io', kind='Role', name=role),
                subjects=[dict(kind='ServiceAccount', name='kps-operator', namespace=release_namespace)])


def transform(documents):
    docs = copy.deepcopy(documents)
    def one(kind, name):
        found = [d for d in docs if d.get('kind') == kind and d['metadata']['name'] == name]
        if len(found) != 1:
            raise ValueError('Unexpected pinned chart resource shape')
        return found[0]

    release_namespace = one('Deployment', 'kps-operator')['metadata']['namespace']
    operator = one('ClusterRole', 'kps-operator')
    if operator['metadata']['labels'].get('chart') != 'kube-prometheus-stack-91.8.2':
        raise ValueError('Unsupported chart version')
    original = operator['rules']
    deployment = one('Deployment', 'kps-operator')
    required_args = [f'--namespaces={release_namespace}', f'--prometheus-instance-namespaces={release_namespace}',
                     f'--alertmanager-instance-namespaces={release_namespace}', f'--alertmanager-config-namespaces={release_namespace}',
                     f'--thanos-ruler-instance-namespaces={release_namespace}', '--watch-referenced-objects-in-all-namespaces=false']
    if (deployment['metadata']['namespace'] != release_namespace or
        not all(a in deployment['spec']['template']['spec']['containers'][0]['args'] for a in required_args)):
        raise ValueError('Complete scoped values required')
    # Keep original names/bindings so Helm replaces the live broad ClusterRole.
    operator['rules'] = [r for r in original if r['resources'] in
                         [['nodes'], ['namespaces'], ['storageclasses']]]
    if len(operator['rules']) != 3:
        raise ValueError('Unexpected cluster discovery rules')
    monitoring = copy.deepcopy(operator)
    monitoring['kind'] = 'Role'
    monitoring['metadata']['namespace'] = release_namespace
    monitoring['rules'] = [r for r in original if r['resources'] not in
                           [['nodes'], ['namespaces'], ['storageclasses'], ['ingresses']]]
    system_namespace = 'kube-system'
    for arg in deployment['spec']['template']['spec']['containers'][0]['args']:
        if arg.startswith('--kubelet-service='):
            system_namespace = arg.split('=', 1)[1].split('/')[0]
    # Kubelet synchronizer creates the named Service/Endpoints in kube-system.
    # RBAC cannot resourceName-limit CREATE; all later mutations are name-limited.
    kubelet = dict(apiVersion=API, kind='Role', metadata=dict(name='kps-operator-kubelet', namespace=system_namespace),
                   rules=[rule([''], ['services', 'endpoints'], ['create']),
                          rule([''], ['services', 'endpoints', 'services/finalizers'],
                               ['get', 'update', 'delete'], ['kps-kubelet'])])
    grafana = one('Role', 'kps-grafana')
    if grafana['metadata']['namespace'] != release_namespace:
        raise ValueError('Unexpected Grafana namespace')
    # Upstream namespaced Role still grants Secrets even for ConfigMap-only sidecars.
    grafana['rules'] = [rule([''], ['configmaps'], ['get', 'list', 'watch'])]
    for kind in ['ClusterRole', 'ClusterRoleBinding']:
        if any(d.get('kind') == kind and d['metadata']['name'].startswith('kps-grafana') for d in docs):
            raise ValueError('Grafana must use namespaced RBAC')
    ksm = one('ClusterRole', 'kps-kube-state-metrics')
    if any('secrets' in r.get('resources', []) for r in ksm['rules']):
        raise ValueError('Secret collector remains enabled')
    # Helm excludes hooks from post-render input. Certgen's separate SA, Secret
    # Role and webhook permissions remain chart-owned, not operator grants.
    docs.extend([monitoring, binding('kps-operator', release_namespace, 'kps-operator', release_namespace),
                 kubelet, binding('kps-operator-kubelet', system_namespace, 'kps-operator-kubelet', release_namespace)])
    return docs


if __name__ == '__main__':
    # Helm streams UTF-8; Windows console codepages must not reinterpret YAML.
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        result = transform([d for d in yaml.safe_load_all(sys.stdin) if d])
    except Exception as error:
        sys.exit('Monitoring post-render failed (' + type(error).__name__ +
                 '): check pinned chart and complete values (input withheld).')
    yaml.safe_dump_all(result, sys.stdout, sort_keys=False)

#!/usr/bin/env python3
"""Offline JSON case generation. Does not call kubectl or read Secrets."""
import copy
import json
import yaml
try:
    from check_admission import ARC, POOLS, CONTROLLER, build_cases, runner_pod
except ImportError:
    from tests.check_admission import ARC, POOLS, CONTROLLER, build_cases, runner_pod


def cases():
    pods = {pool: runner_pod(yaml.safe_load(values.read_text(encoding='utf-8'))['template'], pool)
            for pool, (_, _, values) in POOLS.items()}
    result = build_cases(pods['arc-light'], pods['agent-array-arc-heavy'], pods['arc-light'], pods['agent-array-arc-heavy'])
    plain = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': 'arc-ordinary-check', 'namespace': 'default'},
             'spec': {'containers': [{'name': 'busybox', 'image': 'busybox:1.36'}]}}
    deployment = {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': copy.deepcopy(plain['metadata']),
                  'spec': {'selector': {'matchLabels': {'app': 'arc-ordinary-check'}},
                           'template': {'metadata': {'labels': {'app': 'arc-ordinary-check'}},
                                        'spec': copy.deepcopy(plain['spec'])}}}
    minimal = copy.deepcopy(plain)
    minimal['metadata']['namespace'] = 'agent-array-arc-runners'
    minimal['spec'].update(runtimeClassName=POOLS['arc-light'][1], automountServiceAccountToken=False)
    result += [('ordinary plain pod', True, plain, None, None),
               ('ordinary Deployment', True, deployment, None, None),
               ('light optional fields absent', True, minimal, None, None)]
    for prefix in ('dev.gvisor.', 'io.gvisor.'):
        bad = copy.deepcopy(minimal)
        bad['metadata']['annotations'] = {prefix + 'net': 'host'}
        result.append((prefix + 'override', False, bad, 'annotations', None))
    return [{'name': n, 'allow': allow, 'object': obj, 'message': msg,
             'as': user or (CONTROLLER if obj['metadata'].get('namespace') in ('agent-array-arc-runners', 'agent-array-arc-heavy') else None)}
            for n, allow, obj, msg, user in result]


if __name__ == '__main__':
    print(json.dumps(cases(), indent=2))

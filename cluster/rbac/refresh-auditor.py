#!/usr/bin/env python3
"""Create reviewable auditor rules from an offline APIResourceList snapshot.

Input is a JSON list of Kubernetes APIResourceList objects, collected by a
platform admin. No cluster access is performed. Exit 2 means invalid input.
"""
import argparse
import json
from pathlib import Path


def role(lists):
    grouped = {}
    for listing in lists:
        version = listing['groupVersion']
        group = version.split('/')[0] if '/' in version else ''
        for resource in listing['resources']:
            name = resource['name']
            # Credential-bearing CRDs require a separate explicit exception.
            if name.split('/')[0] in {'secrets', 'sealedsecrets'}:
                continue
            if '/' in name and name != 'pods/log':
                continue
            verbs = sorted(set(resource.get('verbs', [])) & {'get', 'list', 'watch'})
            if verbs:
                grouped.setdefault((group, tuple(verbs)), set()).add(name)
    rules = [{'apiGroups': [group], 'resources': sorted(names), 'verbs': list(verbs)}
             for (group, verbs), names in sorted(grouped.items())]
    return {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'ClusterRole',
            'metadata': {'name': 'aa-auditor'}, 'rules': rules}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--discovery', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = role(json.loads(args.discovery.read_text()))
    except (ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Validate an externally rendered Wazuh manifest without printing its contents."""
import argparse
import re
import yaml


def validate(documents):
    for document in documents:
        kind = document['kind']
        if kind == 'Secret':
            raise ValueError('Credentials must be provisioned separately')
        if kind == 'Service':
            spec = document['spec']
            assert spec.get('type', 'ClusterIP') == 'ClusterIP'
            assert all('nodePort' not in port for port in spec['ports'])
        if kind in ['Deployment', 'StatefulSet', 'DaemonSet']:
            spec = document['spec']['template']['spec']
            assert spec['automountServiceAccountToken'] is False
            assert not spec.get('hostNetwork', False)
            for container in spec.get('initContainers', []) + spec['containers']:
                assert re.search(r'@sha256:[0-9a-f]{64}$', container['image'])
                assert not container.get('securityContext', {}).get('privileged', False)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest')
    args = parser.parse_args(argv)
    try:
        with open(args.manifest, encoding='utf-8') as stream:
            validate([doc for doc in yaml.safe_load_all(stream) if doc])
    except Exception:
        parser.exit(1, 'Wazuh server render failed safety checks (input withheld).\n')
    print('PASS: Wazuh server pins, ClusterIP, token and privilege checks')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Check Velero policy and a local volume inventory. Exit 1 on an unprotected login volume."""
import argparse
import json
from pathlib import Path, PurePosixPath

import yaml


def validate(policy, volumes, login_root, required, login_class):
    if login_root not in required:
        raise ValueError('required exclusions omit login root')
    data = yaml.safe_load(policy['data']['resource-policies.yaml'])
    skip_classes = set()
    for rule in data['volumePolicies']:
        if rule['action']['type'] == 'skip':
            skip_classes.update(rule['conditions'].get('storageClass', []))
    if login_class not in skip_classes:
        raise ValueError('login storage class is not skipped')
    for volume in volumes:
        spec = volume.get('spec', {})
        path = spec.get('local', {}).get('path') or spec.get('hostPath', {}).get('path')
        if not path:
            continue
        protected = any(PurePosixPath(path) == PurePosixPath(excluded)
                        or PurePosixPath(excluded) in PurePosixPath(path).parents for excluded in required)
        if protected and (spec.get('storageClassName') not in skip_classes or
                          volume.get('metadata', {}).get('labels', {}).get('velero.io/exclude-from-backup') != 'true'):
            raise ValueError('excluded host volume lacks skip class or exclusion label')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--policy', type=Path, required=True)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--login-root', required=True)
    parser.add_argument('--exclusions-json', required=True)
    parser.add_argument('--login-class', required=True)
    args = parser.parse_args(argv)
    try:
        policy = yaml.safe_load(args.policy.read_text())
        inventory = yaml.safe_load(args.inventory.read_text())
        validate(policy, inventory.get('items', []), args.login_root,
                 json.loads(args.exclusions_json), args.login_class)
        print('Velero login exclusion inventory: OK')
        return 0
    except (ValueError, OSError, KeyError, TypeError, yaml.YAMLError):
        print('Velero exclusion validation failed')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Print node contract commands; --yes executes them. Exit 2: invalid input."""
import argparse
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess


def commands(data):
    name = data['node']
    prefix = data['label_prefix']
    if not re.fullmatch(r'[a-z0-9][a-z0-9.-]*', name):
        raise ValueError('invalid node name')
    if not re.fullmatch(r'[a-z0-9][a-z0-9.-]*', prefix):
        raise ValueError('invalid label prefix')
    labels = {}
    for kind, values in [('role', data['roles']), ('runtime', data['runtime_classes'])]:
        for value in values:
            if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', value):
                raise ValueError('invalid role/runtime class')
            labels[f'{prefix}/{kind}-{value}'] = 'true'
    for key, value in data.get('labels', {}).items():
        if key.startswith(prefix + '/'):
            labels[key] = str(value)
    result = [['kubectl', 'label', 'node', name, '--overwrite',
               *[f'{key}={value}' for key, value in sorted(labels.items())]]]
    if 'gpu' in data['roles']:
        result.append(['kubectl', 'taint', 'node', name,
                       f'{prefix}/gpu=true:NoSchedule', '--overwrite'])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rendered', required=True, type=Path)
    parser.add_argument('--yes', action='store_true')
    args = parser.parse_args(argv)
    files = sorted(args.rendered.glob('nodes/*/cluster/node-contract/labels.json'))
    if not files:
        parser.error('no rendered node contracts found')
    try:
        planned = [cmd for path in files for cmd in commands(json.loads(path.read_text()))]
    except (KeyError, ValueError, TypeError) as exc:
        parser.error(str(exc))
    for cmd in planned:
        print(shlex.join(cmd))
        if args.yes:
            executable = shutil.which(cmd[0])
            if not executable:
                parser.error('kubectl not found on PATH')
            subprocess.run([executable, *cmd[1:]], check=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Create a synthetic all-module org config without changing scalar types."""
import argparse
import hashlib
from pathlib import Path

import yaml


class QuotedStrings(yaml.SafeDumper):
    """The renderer's strict subset requires time-like strings to stay quoted."""

    def represent_mapping(self, tag, mapping, flow_style=None):
        node = super().represent_mapping(tag, mapping, flow_style)
        # A block-list mapping starts with a plain key in the strict subset.
        for key, _ in node.value:
            key.style = None
        return node


def quoted_string(dumper, value):
    return dumper.represent_scalar('tag:yaml.org,2002:str', value, style='"')


QuotedStrings.add_representer(str, quoted_string)


def create(source, target, strict_fixture=False):
    model = yaml.safe_load(source.read_text(encoding='utf-8'))
    # Use copied registries rather than the repository originals in temporary fixtures.
    for name, reference in model['files'].items():
        if reference.startswith('org/'):
            model['files'][name] = (target / Path(reference).name).resolve().as_posix()
    for value in model.get('modules', {}).values():
        value['enabled'] = True
    # Enabled GPU defaults deliberately refuse deployment until artifacts are verified.
    # CI exercises their shapes with synthetic pins; it never downloads these models.
    gpu_nodes = [node['name'] for node in model['nodes'] if 'gpu' in node['roles']]
    defaults = source.resolve().parents[1] / 'modules/gpu-lanes/org.component.defaults.yaml'
    gpu = model.get('modules', {}).get('gpu-lanes')
    if gpu is not None and gpu_nodes:
        lanes = gpu.get('lanes') or yaml.safe_load(defaults.read_text())['defaults']['lanes']
        for index, lane in enumerate(lanes):
            lane['node'] = gpu_nodes[index % len(gpu_nodes)]
            lane['gguf_sha256'] = hashlib.sha256(('synthetic-ci-' + lane['name']).encode()).hexdigest()
        gpu['lanes'] = lanes
    if strict_fixture:
        for name, image in model['images'].items():
            image['digest'] = 'sha256:' + hashlib.sha256(('synthetic-ci-image-' + name).encode()).hexdigest()
        model.setdefault('components', {}).setdefault('supervisor', {})['image'] = (
            'example.invalid/supervisor:ci@sha256:' + hashlib.sha256(b'synthetic-ci-supervisor').hexdigest())
    target.mkdir(parents=True, exist_ok=True)
    for example in source.parent.glob('*.example.yaml'):
        text = example.read_text(encoding='utf-8')
        if strict_fixture and example.name == 'users.example.yaml':
            session_node = next(n['name'] for n in model['nodes'] if 'sessions' in n['roles'])
            text = text.replace('home_node: auto', 'home_node: ' + session_node)
            text = text.replace('home_node: "auto"', 'home_node: "' + session_node + '"')
        (target / example.name).write_text(text,
                                         encoding='utf-8', newline='\n')
    (target / source.name).write_text('# Synthetic CI fixture; never deploy its model pins.\n' +
                                     yaml.dump(model, Dumper=QuotedStrings, sort_keys=False,
                                               width=100000),
                                     encoding='utf-8', newline='\n')
    return model


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('org/org.example.yaml'))
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--strict-fixture', action='store_true',
                        help='Use synthetic image pins and explicit nodes for offline strict-path tests only')
    args = parser.parse_args()
    model = create(args.source, args.out, args.strict_fixture)
    print('All modules enabled; Kubernetes version: ' + str(model['cluster']['version']))


if __name__ == '__main__':
    main()

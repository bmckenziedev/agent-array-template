#!/usr/bin/env python3
"""Regenerate dashboard templates; --check reports stale files without writing."""
import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def render(source: Path) -> str:
    dashboard = json.loads(source.read_text(encoding='utf-8'))
    lines = ['apiVersion: v1', 'kind: ConfigMap', 'metadata:',
             f'  name: grafana-dashboard-{source.stem}', '  namespace: "{{NS_MONITORING}}"',
             '  labels:', '    grafana_dashboard: "1"',
             '    app.kubernetes.io/part-of: "{{PROJECT_NAME}}"',
             '    app.kubernetes.io/component: dashboards', 'data:', f'  {source.name}: |-']
    lines.extend('    ' + line for line in json.dumps(dashboard, indent=2).splitlines())
    return '\n'.join(lines) + '\n'


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    stale = []
    for source in sorted(HERE.glob('*.json')):
        # Dev previews remain outside k8s; the plugin emits deployed ConfigMaps.
        target = HERE / (source.stem + '-configmap.yaml')
        text = render(source)
        if target.exists() and target.read_text(encoding='utf-8') == text:
            continue
        if args.check:
            stale.append(target.name)
        else:
            target.write_text(text, encoding='utf-8', newline='\n')
    if stale:
        print('Stale dashboard templates: ' + ', '.join(stale))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

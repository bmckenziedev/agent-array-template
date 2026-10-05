#!/usr/bin/env python3
"""Render rule fixtures in a temporary directory and run local promtool."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[1]
KEY_RE = re.compile(r'\{\{([A-Z][A-Z0-9_]*)\}\}')


def fixture_keys():
    model = json.loads((ROOT / 'tests/fixtures/org.fixture.json').read_text())
    keys = dict(model['keys'])
    defaults = yaml.safe_load((ROOT / 'org.component.defaults.yaml').read_text())['defaults']
    for key, value in defaults.items():
        keys['C_MONITORING_' + key.upper()] = str(value)
    keys['C_MONITORING_KSM_LABEL_PREFIX'] = re.sub('[^a-zA-Z0-9_]', '_', keys['LABEL_PREFIX'])
    keys['C_MONITORING_KSM_RUNTIME_VM_LABEL'] = 'label_' + re.sub(
        '[^a-zA-Z0-9_]', '_', keys['LABEL_PREFIX'] + '/runtime-' + keys['RUNTIME_CLASS_VM'])
    return keys


def substitute(text, keys):
    return KEY_RE.sub(lambda match: keys[match.group(1)], text)


def stage(destination: Path) -> tuple[list[Path], list[Path]]:
    keys = fixture_keys()
    rule_dir, test_dir = destination / 'rules-extracted', destination / 'tests'
    rule_dir.mkdir(parents=True)
    test_dir.mkdir()
    rules, tests = [], []
    for source in sorted((ROOT / 'alerts/rules').glob('*.yaml')):
        doc = yaml.safe_load(substitute(source.read_text(), keys))
        assert doc['kind'] == 'PrometheusRule'
        target = rule_dir / source.name
        target.write_text(yaml.safe_dump(doc['spec'], sort_keys=False), encoding='utf-8', newline='\n')
        rules.append(target)
    spec=importlib.util.spec_from_file_location('monitoring_render',ROOT/'render_plugin.py')
    plugin=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    model=json.loads((ROOT/'tests/fixtures/org.fixture.json').read_text())
    emitted={}
    plugin.render(model,emitted.__setitem__)
    account_info=json.loads(emitted['global/monitoring/k8s/account-info.yaml'])
    target=rule_dir/'account-info.rules.yaml'
    target.write_text(yaml.safe_dump(account_info['spec'],sort_keys=False),encoding='utf-8',newline='\n')
    rules.append(target)
    dashboard_queries = []
    def queries(value):
        if isinstance(value,dict):
            for key,item in value.items():
                if key=='expr' and isinstance(item,str):
                    expression=substitute(item,keys)
                    expression=expression.replace('$__rate_interval','5m').replace('$__interval','1m')
                    expression=expression.replace('$__range','1h')
                    expression=re.sub(r'\$\{[^}]+\}|\$(?:node|host|device|lane|namespace|team)', '.+', expression)
                    dashboard_queries.append(dict(record=f'dashboard_query_{len(dashboard_queries)}',expr=expression))
                else:
                    queries(item)
        elif isinstance(value,list):
            for item in value:
                queries(item)
    for source in sorted((ROOT/'dashboards').glob('*.json')):
        queries(json.loads(source.read_text()))
    if dashboard_queries:
        target=rule_dir/'dashboards.rules.yaml'
        target.write_text(yaml.safe_dump(dict(groups=[dict(name='dashboard.syntax',rules=dashboard_queries)]),
                                        sort_keys=False),encoding='utf-8',newline='\n')
        rules.append(target)
    for source in sorted((ROOT / 'alerts/tests').glob('*.test.yaml')):
        doc = yaml.safe_load(substitute(source.read_text(), keys))
        assert doc['tests'] and all((test_dir / rule).resolve().is_file() for rule in doc['rule_files'])
        target = test_dir / source.name
        target.write_text(yaml.safe_dump(doc, sort_keys=False), encoding='utf-8', newline='\n')
        tests.append(target)
    return rules, tests


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--promtool', default=os.environ.get('PROMTOOL', shutil.which('promtool')))
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix='monitoring-rules-') as directory:
        rules, tests = stage(Path(directory))
        print(f'PASS: PyYAML parsed {len(rules)} rule files and {len(tests)} test files')
        if not args.promtool:
            print('SKIP: promtool unavailable; no rule evaluation proof')
            return 0
        subprocess.run([args.promtool, 'check', 'rules', *map(str, rules)], check=True)
        subprocess.run([args.promtool, 'test', 'rules', *map(str, tests)], check=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

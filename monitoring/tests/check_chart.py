#!/usr/bin/env python3
"""Offline real-chart integration check; never prints rendered manifest bodies."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name,path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--helm',required=True)
    parser.add_argument('--chart',required=True)
    args = parser.parse_args(argv)
    validate = load('validate_rules',ROOT/'alerts/validate.py')
    plugin = load('routing_plugin',ROOT/'render_plugin.py')
    post = load('rbac_post',ROOT/'kube-prometheus-stack/post_render.py')
    model = json.loads((ROOT/'tests/fixtures/org.fixture.json').read_text())
    emitted = {}
    plugin.render(model,emitted.__setitem__)
    with tempfile.TemporaryDirectory(prefix='monitoring-chart-') as temp:
        work = Path(temp)
        base, routing = work/'base.yaml',work/'routing.yaml'
        base.write_text(plugin.subst((ROOT/'helm/kube-prometheus-stack/values.tmpl.yaml').read_text(),
                                    validate.fixture_keys()),encoding='utf-8',newline='\n')
        routing.write_text(emitted['files/monitoring/helm/kube-prometheus-stack/routing.values.yaml'],
                           encoding='utf-8',newline='\n')
        process = subprocess.run([args.helm,'template','kps',args.chart,'-n',model['keys']['NS_MONITORING'],
                                  '--kube-version',model['keys']['K8S_VERSION'],'-f',str(base),'-f',str(routing)],
                                 capture_output=True,text=True,encoding='utf-8')
        if process.returncode:
            parser.exit(1,'Pinned Helm render failed (output withheld).\n')
        raw = [doc for doc in yaml.safe_load_all(process.stdout) if doc]
        documents = post.transform(raw)
        images = []
        for doc in documents:
            if doc['kind'] in ['Prometheus','Alertmanager']:
                assert re.search(r'@sha256:[0-9a-f]{64}$',doc['spec']['image'])
            if doc['kind'] not in ['Deployment','DaemonSet','StatefulSet','Job']:
                continue
            spec = doc['spec']['template']['spec']
            images.extend(container['image'] for container in spec.get('initContainers',[])+spec['containers'])
        assert images and all(re.search(r'@sha256:[0-9a-f]{64}$',image) for image in images)
        roles = [doc for doc in documents if doc['kind'] in ['Role','ClusterRole']]
        for role in roles:
            if role['metadata']['name'] in ['kps-grafana','kps-kube-state-metrics']:
                assert all('secrets' not in rule['resources'] for rule in role['rules'])
        operator = next(doc for doc in documents if doc['kind']=='ClusterRole' and doc['metadata']['name']=='kps-operator')
        assert {r for rule in operator['rules'] for r in rule['resources']} == {'nodes','namespaces','storageclasses'}
        deployment = next(doc for doc in documents if doc['kind']=='Deployment' and doc['metadata']['name']=='kps-grafana')
        for container in deployment['spec']['template']['spec']['containers']:
            if container['name'].startswith('grafana-sc-'):
                environment = {entry['name']:entry.get('value') for entry in container['env']}
                assert environment['RESOURCE']=='configmap'
                assert environment['NAMESPACE']==model['keys']['NS_MONITORING']
        print(f'PASS: real chart 91.8.2 rendered {len(documents)} objects; {len(images)} images digest-pinned; scoped operator, KSM/Grafana RBAC and ConfigMap sidecars')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Render pinned server workload sources with metadata-only generated inputs.
Never fetches certificate, secret, password, or internal_users files.
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request
import yaml

ROOT = Path(__file__).resolve().parent
COMMIT = '2c2d13c550248b1fe91a5c3e3671de06a6180769'
SOURCES = [
    'base/storage-class.yaml', 'base/wazuh-ns.yaml',
    'indexer_stack/wazuh-dashboard/dashboard-deploy.yaml',
    'indexer_stack/wazuh-dashboard/dashboard-svc.yaml',
    'indexer_stack/wazuh-indexer/cluster/indexer-api-svc.yaml',
    'indexer_stack/wazuh-indexer/cluster/indexer-sts.yaml',
    'indexer_stack/wazuh-indexer/indexer-svc.yaml',
    'wazuh_managers/wazuh-cluster-svc.yaml',
    'wazuh_managers/wazuh-master-sts.yaml',
    'wazuh_managers/wazuh-master-svc.yaml',
    'wazuh_managers/wazuh-worker-sts.yaml',
    'wazuh_managers/wazuh-workers-svc.yaml',
]


def fetch_and_render() -> None:
    with tempfile.TemporaryDirectory(prefix='wazuh-server-render-') as temp:
        work = Path(temp)
        base = work/'wazuh'
        overlay = work/'envs'/'agent-array'
        base.mkdir()
        overlay.mkdir(parents=True)
        resources = []
        for i, path in enumerate(SOURCES):
            url = f'https://raw.githubusercontent.com/wazuh/wazuh-kubernetes/{COMMIT}/wazuh/{path}'
            content = urllib.request.urlopen(url, timeout=30).read().decode()
            docs = list(yaml.safe_load_all(content))
            assert all(d['kind'] in ('StatefulSet','Deployment','Service','Namespace','StorageClass') for d in docs if d)
            filename = f'workload-{i}.yaml'
            (base/filename).write_text(content)
            resources.append(filename)
        # Names only allow the existing delete patches to resolve; no Secret data.
        names = ('indexer-cred','dashboard-cred','wazuh-api-cred','wazuh-authd-pass','wazuh-cluster-key')
        stubs = [{'apiVersion':'v1','kind':'Secret','metadata':{'name':n,'namespace':'wazuh'}} for n in names]
        stubs += [{'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':n}} for n in ('indexer-conf','dashboard-conf')]
        (base/'metadata-only.yaml').write_text(yaml.safe_dump_all(stubs))
        resources.append('metadata-only.yaml')
        (base/'kustomization.yaml').write_text(yaml.safe_dump({'resources':resources}))
        for src in (ROOT/'../../rendered/files/modules/wazuh/overlay').glob('*.yaml'):
            shutil.copyfile(src, overlay/src.name)
        shutil.copyfile(ROOT/'../../rendered/global/modules/wazuh/k8s/networkpolicy.yaml',overlay/'networkpolicy.yaml')
        k = yaml.safe_load((ROOT/'../../rendered/files/modules/wazuh/overlay/kustomization.yml').read_text())
        # Production-generated credentials/certificates are outside this test's scope.
        for generator in k['configMapGenerator']:
            generator.pop('files')
            generator['literals'] = ['boundary-render=metadata-only']
        (overlay/'kustomization.yaml').write_text(yaml.safe_dump(k))
        docs = list(yaml.safe_load_all(subprocess.check_output(['kubectl','kustomize',str(overlay)],text=True)))
        assert not any(d['kind']=='Secret' for d in docs)
        assert len([d for d in docs if d['kind']=='NetworkPolicy']) == 6
        pods = [d for d in docs if d['kind'] in ('Deployment','StatefulSet')]
        assert {d['metadata']['name'] for d in pods} == {'wazuh-manager-master','wazuh-indexer','wazuh-dashboard'}
        for pod in pods:
            spec = pod['spec']['template']['spec']
            assert spec['automountServiceAccountToken'] is False
            assert not spec.get('hostNetwork')
            assert pod['spec']['template']['metadata']['labels']['app'] in ('wazuh-manager','wazuh-indexer','wazuh-dashboard')
            for c in spec.get('initContainers',[])+spec['containers']:
                assert '@sha256:' in c['image']
                assert not c.get('securityContext',{}).get('privileged')
        for service in (d for d in docs if d['kind']=='Service'):
            assert service['spec'].get('type','ClusterIP')=='ClusterIP'
            assert all('nodePort' not in p for p in service['spec']['ports'])
        manager = next(d for d in docs if d['kind']=='Service' and d['metadata']['name']=='wazuh')
        assert {1514,1515,55000} <= {p['port'] for p in manager['spec']['ports']}
        print(f'PASS: pinned server workload overlay rendered ({len(docs)} objects); 6 policies, expected labels/ports, ClusterIP, no Secrets, unchanged image pins')
        print('LIMIT: generated credential/TLS ConfigMaps and Secrets omitted; production installation render and runtime not validated')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true', help='Explicitly fetch the pinned upstream workload files')
    args = parser.parse_args(argv)
    if not args.fetch:
        parser.error('Network-dependent validation requires --fetch; it is never part of offline tests')
    fetch_and_render()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Render audit policy assets only when the Wazuh module is enabled."""
import json
from pathlib import Path
import re

PLUGIN_NAME = 'wazuh-audit'
HERE = Path(__file__).resolve().parent
KEY_RE = re.compile(r'\{\{([A-Z][A-Z0-9_]*)\}\}')


def render(model, emit):
    if not model['org'].get('modules',{}).get('wazuh',{}).get('enabled',False):
        return
    keys = model['keys']
    text = (HERE/'audit/wazuh-k8s-audit-rules.tmpl.xml').read_text(encoding='utf-8')
    text = KEY_RE.sub(lambda match: keys[match.group(1)],text)
    document = dict(apiVersion='v1',kind='ConfigMap',metadata=dict(
        name='wazuh-organization-audit',namespace=keys['PROJECT_NAME']+'-wazuh',
        labels={'app.kubernetes.io/part-of': keys['PROJECT_NAME'],
                'app.kubernetes.io/name':'wazuh-manager','app.kubernetes.io/component':'security'}),
        data={'rules.xml':text})
    emit('global/modules/wazuh/k8s/audit-rules.yaml',json.dumps(document,indent=2,sort_keys=True)+'\n')
    # kube-router may evaluate after Service DNAT; preserve endpoint and Service paths.
    endpoints = json.loads(keys['APISERVER_ENDPOINT_IPS_JSON'])
    document = dict(apiVersion='networking.k8s.io/v1',kind='NetworkPolicy',metadata=dict(
        name='wazuh-api-endpoints',namespace=keys['PROJECT_NAME']+'-wazuh',
        labels={'app.kubernetes.io/part-of':keys['PROJECT_NAME'],'app.kubernetes.io/component':'security'}),
        spec=dict(podSelector={},policyTypes=['Egress'],egress=[
            dict(to=[dict(ipBlock=dict(cidr=ip+'/32')) for ip in endpoints],
                 ports=[dict(protocol='TCP',port=int(keys['APISERVER_PORT']))]),
            dict(to=[dict(ipBlock=dict(cidr=keys['APISERVER_SERVICE_IP']+'/32'))],
                 ports=[dict(protocol='TCP',port=443)]),
        ]))
    emit('global/modules/wazuh/k8s/api-endpoints.yaml',json.dumps(document,indent=2,sort_keys=True)+'\n')

"""Evaluate the policy's actual CEL, not a Python approximation.

python -B -m unittest discover -s platform/hardening/tests -t platform/hardening -v
Dependencies: PyYAML==6.0.3 cel-python==0.4.0 (install in a call-owned temp venv).
This is offline evaluation; apiserver type checking remains a rollout prerequisite.
"""
import copy
import pathlib
import unittest
import yaml
from celpy import Environment, json_to_cel, celtypes

from render_plugin import subst
import json
ROOT = pathlib.Path(__file__).parents[1]
KEYS = json.loads((ROOT / 'tests/fixtures/org.fixture.json').read_text())['keys']
POLICY = list(yaml.safe_load_all(subst((ROOT / 'k8s/monitoring-host-access.tmpl.yaml').read_text(), KEYS)))[0]['spec']
OPERATOR = 'system:serviceaccount:' + KEYS['NS_MONITORING'] + ':kps-operator'
CONTROLLER = 'system:serviceaccount:kube-system:daemon-set-controller'


def admitted(obj, username=OPERATOR, operation='CREATE', old=None):
    env = Environment()
    context = json_to_cel(dict(object=obj, oldObject=old,
                   request=dict(userInfo=dict(username=username), operation=operation)))
    def evaluate(expression):
        result = env.program(env.compile(expression)).evaluate(context)
        return result
    if not all(evaluate(c['expression']) for c in POLICY['matchConditions']):
        return True
    context['variables'] = celtypes.MapType()
    for variable in POLICY['variables']:
        context['variables'][variable['name']] = evaluate(variable['expression'])
    return all(evaluate(v['expression']) for v in POLICY['validations'])


def pod():
    return dict(apiVersion='v1', kind='Pod', metadata=dict(name='ordinary', namespace=KEYS['NS_MONITORING']),
                spec=dict(containers=[dict(name='busybox', image='busybox:1.36')]))


def workload(kind, spec):
    template = dict(metadata=dict(labels=dict(app='ordinary')), spec=copy.deepcopy(spec))
    body = dict(template=template)
    if kind == 'CronJob':
        body = dict(schedule='0 * * * *', jobTemplate=dict(spec=body))
    return dict(apiVersion='batch/v1' if kind in ['Job', 'CronJob'] else 'apps/v1',
                kind=kind, metadata=dict(name='ordinary', namespace=KEYS['NS_MONITORING']), spec=body)


class HostAccess(unittest.TestCase):
    def test_minimal_workloads_no_optional_fields(self):
        ordinary = pod()
        self.assertTrue(admitted(ordinary))
        for kind in ['Deployment', 'StatefulSet', 'DaemonSet', 'ReplicaSet', 'Job', 'CronJob']:
            with self.subTest(kind=kind):
                self.assertTrue(admitted(workload(kind, ordinary['spec'])))

    def test_denied_host_access_and_operator_outside_monitoring(self):
        for namespace in [KEYS['NS_MONITORING'], 'default', 'other']:
            for field, value in [('hostPID', True), ('hostIPC', True), ('hostNetwork', True),
                                 ('nodeName', 'node-b'), ('volumes', [dict(name='root', hostPath=dict(path='/'))])]:
                obj = pod()
                obj['metadata']['namespace'] = namespace
                obj['spec'][field] = value
                with self.subTest(namespace=namespace, field=field):
                    self.assertFalse(admitted(obj))
                    self.assertFalse(admitted(workload('StatefulSet', obj['spec'])))

    def test_all_container_types_and_optional_nested_fields(self):
        for field in ['containers', 'initContainers', 'ephemeralContainers']:
            for extra in [dict(securityContext=dict(privileged=True)),
                          dict(securityContext=dict(procMount='Unmasked')),
                          dict(securityContext=dict(capabilities=dict(add=['SYS_ADMIN']))),
                          dict(ports=[dict(containerPort=9100, hostPort=9100)])]:
                obj = pod()
                obj['spec'][field] = [dict(name='bad', image='busybox:1.36', **extra)]
                with self.subTest(field=field, extra=extra):
                    self.assertFalse(admitted(obj))
            obj = pod()
            obj['spec'][field] = [dict(name='ordinary', image='busybox:1.36', securityContext={}, ports=[dict(containerPort=80)])]
            self.assertTrue(admitted(obj))

    def test_scheduled_update_and_unrelated_scope(self):
        obj = pod()
        obj['spec']['nodeName'] = 'node1'
        self.assertTrue(admitted(obj, operation='UPDATE', old=copy.deepcopy(obj)))
        old = copy.deepcopy(obj)
        obj['spec']['nodeName'] = 'node2'
        self.assertFalse(admitted(obj, operation='UPDATE', old=old))
        obj['metadata']['namespace'] = 'other'
        self.assertTrue(admitted(obj, username='other-controller'))

    def test_real_exporter_and_mutations(self):
        spec = dict(hostPID=True, hostNetwork=True,
                    serviceAccountName='kps-prometheus-node-exporter',
                    automountServiceAccountToken=False,
                    securityContext=dict(runAsUser=65534, runAsGroup=65534, runAsNonRoot=True),
                    containers=[dict(name='node-exporter',
                        image='quay.io/prometheus/node-exporter:v1.12.1-distroless@sha256:8c9bac11973b94b59be88d6e11fee4429aa743c8846cdc75d65b18db33f6a106',
                        securityContext=dict(readOnlyRootFilesystem=True),
                        volumeMounts=[dict(name=name, mountPath='/host/' + name, readOnly=True)
                                      for name in ['root', 'proc', 'sys']])],
                    volumes=[dict(name=name, hostPath=dict(path=path))
                             for name, path in [('root', '/'), ('proc', '/proc'), ('sys', '/sys')]])
        daemonset = workload('DaemonSet', spec)
        daemonset['metadata']['name'] = 'kps-prometheus-node-exporter'
        argo = 'system:serviceaccount:' + KEYS['NS_ARGOCD'] + ':argocd-application-controller'
        self.assertTrue(admitted(daemonset, username=argo))
        self.assertFalse(admitted(daemonset, username=OPERATOR))
        child = pod()
        child['spec'] = copy.deepcopy(spec)
        child['metadata']['ownerReferences'] = [dict(kind='DaemonSet', name='kps-prometheus-node-exporter', controller=True)]
        self.assertTrue(admitted(child, username=CONTROLLER))
        self.assertFalse(admitted(child, username=argo))
        for change in ['image', 'readOnly', 'uid', 'mount', 'command', 'token']:
            obj = copy.deepcopy(daemonset)
            body = obj['spec']['template']['spec']
            container = body['containers'][0]
            if change == 'image':
                container['image'] = 'busybox:1.36'
            elif change == 'readOnly':
                container['volumeMounts'][0]['readOnly'] = False
            elif change == 'uid':
                body['securityContext']['runAsUser'] = 0
            elif change == 'mount':
                body['volumes'][1]['hostPath']['path'] = '/etc'
            elif change == 'command':
                container['command'] = ['sh']
            elif change == 'token':
                body['automountServiceAccountToken'] = True
            with self.subTest(change=change):
                self.assertFalse(admitted(obj, username=argo))

    def test_forged_exporter_is_denied(self):
        obj = pod()
        obj['metadata']['name'] = 'kps-prometheus-node-exporter-forged'
        obj['metadata']['ownerReferences'] = [dict(kind='DaemonSet', name='kps-prometheus-node-exporter', controller=True)]
        obj['spec'].update(hostPID=True, serviceAccountName='kps-prometheus-node-exporter')
        self.assertFalse(admitted(obj))
        self.assertFalse(admitted(obj, username=CONTROLLER))


if __name__ == '__main__':
    unittest.main()

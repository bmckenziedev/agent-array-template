"""Ported admission preparation tests, consuming fixture-rendered Helm values."""
from pathlib import Path
import copy
import json
import os
import re
import tempfile
import unittest
import yaml
from tests import check_admission as check

ROOT = Path(__file__).resolve().parents[1]


def keys():
    model = json.loads((ROOT / 'tests/fixtures/org.fixture.json').read_text())
    model['org']['modules']['arc-ci']['enabled'] = True
    result = dict(model['keys'])
    defaults = yaml.safe_load((ROOT / 'org.component.defaults.yaml').read_text())['defaults']
    for name, value in defaults.items():
        result['M_ARC_CI_' + name.upper()] = str(value).lower() if isinstance(value, bool) else str(value)
    return result


def rendered(path):
    return re.sub(r'\{\{([A-Z][A-Z0-9_]*)\}\}', lambda m: keys()[m[1]], path.read_text())


class PreparationTests(unittest.TestCase):
    def test_generated_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            pools = {}
            for lane, ns, runtime in [('light', 'agent-array-arc-runners', 'gvisor'), ('heavy', 'agent-array-arc-heavy', 'kata')]:
                path = Path(tmp) / (lane + '.yaml')
                path.write_text(rendered(ROOT / f'helm/{lane}/values.tmpl.yaml'))
                pools['arc-' + lane] = (ns, runtime, path)
            check.POOLS.update(pools)
            pods = {name: check.runner_pod(yaml.safe_load(p.read_text())['template'], name)
                    for name, (_, _, p) in pools.items()}
            table = check.build_cases(pods['arc-light'], pods['arc-heavy'], pods['arc-light'], pods['arc-heavy'])
            self.assertGreater(len(table), 40)
            self.assertTrue(any(not allow and 'hostPath' in name for name, allow, *_ in table))
            for name, allow, obj, _, actor in table:
                if allow and 'runner pod (' in name:
                    self.assertEqual(actor, check.CONTROLLER)
                    self.assertFalse(obj['spec']['automountServiceAccountToken'])
            minimal = check.netcheck_pod('agent-array-arc-runners', 'gvisor')['spec']
            for field in ('initContainers', 'ephemeralContainers', 'volumes'):
                self.assertNotIn(field, minimal)
            self.assertNotIn('ports', minimal['containers'][0])

    def test_policy_and_network_structure(self):
        policy, binding = list(yaml.safe_load_all(rendered(ROOT / 'k8s/admission.tmpl.yaml')))
        expressions = '\n'.join(v['expression'] for v in policy['spec']['validations'])
        self.assertIn(check.CONTROLLER, expressions)
        self.assertIn('has(e.valueFrom.secretKeyRef.key)', expressions)
        self.assertIn('io.gvisor.', expressions)
        self.assertEqual(binding['spec']['matchResources']['namespaceSelector']['matchExpressions'][0]['values'],
                         ['agent-array-arc-runners', 'agent-array-arc-heavy'])
        for pool in ('runners', 'heavy'):
            deny, allow = list(yaml.safe_load_all(rendered(ROOT / f'k8s/networkpolicy-arc-{pool}.tmpl.yaml')))
            public = allow['spec']['egress'][1]
            self.assertEqual(public['ports'], [{'protocol': 'TCP', 'port': p} for p in (22, 80, 443)])
            self.assertIn('203.0.113.10/32', public['to'][0]['ipBlock']['except'])

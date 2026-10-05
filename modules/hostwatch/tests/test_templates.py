"""Offline module templates and collector installation guard checks."""
import json
from pathlib import Path
import re
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class Templates(unittest.TestCase):
    def test_module_rule_template(self):
        keys = json.loads((ROOT/'tests/fixtures/org.fixture.json').read_text())['keys']
        text = (ROOT/'k8s/hostwatch.rules.tmpl.yaml').read_text()
        text = re.sub(r'\{\{([A-Z][A-Z0-9_]*)\}\}',lambda match:keys[match.group(1)],text)
        doc = yaml.safe_load(text)
        self.assertEqual(doc['kind'],'PrometheusRule')
        self.assertEqual(doc['metadata']['namespace'],keys['NS_MONITORING'])
        self.assertEqual(len(doc['spec']['groups'][0]['rules']),15)

    def test_install_guard_precedes_mutation(self):
        text = (ROOT/'install.sh').read_text()
        self.assertLess(text.index('$1 == --yes'),text.index('install -d'))


if __name__ == '__main__':
    unittest.main()

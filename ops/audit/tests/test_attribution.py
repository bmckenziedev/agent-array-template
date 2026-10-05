import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout

from audit_log_probe import main
from tests.test_audit_log_probe import GOOD, P


class AttributionTests(unittest.TestCase):
    def probe(self, events):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'audit.jsonl'
            path.write_text('\n'.join(json.dumps(event) for event in events))
            with redirect_stdout(io.StringIO()):
                return main([str(path), P, '0', '--actor', 'oidc:example-sub',
                             '--namespace', 'aa-u-ana', '--require-groups'])

    def test_oidc_user_groups_and_session_namespace(self):
        events = json.loads(json.dumps(GOOD))
        for event in events:
            event['user'] = {'username': 'oidc:example-sub', 'groups': ['team-example']}
            event['objectRef']['namespace'] = 'aa-u-ana'
        self.assertEqual(self.probe(events), 0)
        events[0]['user']['groups'] = []
        self.assertEqual(self.probe(events), 1)

    def test_other_user_probe_does_not_pass(self):
        self.assertEqual(self.probe(GOOD), 1)

import json
import unittest
from pathlib import Path
from gateway import authorize, memory_path, validate
from tests.test_templates import fixture, subst


class ConfigTests(unittest.TestCase):
    def config(self):
        model, keys = fixture()
        return model, json.loads(subst((Path(__file__).parents[1] / 'config.tmpl.json').read_text(), keys))

    def test_fixture_identity_mapping_is_team_gated(self):
        model, cfg = self.config()
        validate(cfg)
        self.assertEqual(authorize('example-id', {'example-id': 'bo'}, model['users'], cfg), 'bo')
        with self.assertRaises(PermissionError):
            authorize('example-id', {'example-id': 'ana'}, model['users'], cfg)
        with self.assertRaises(PermissionError):
            authorize('unknown', {}, model['users'], cfg)

    def test_offboarding_and_memory_isolation(self):
        model, cfg = self.config()
        user = next(u for u in model['users'] if u['slug'] == 'bo')
        user['status'] = 'offboarded'
        with self.assertRaises(PermissionError):
            authorize('id', {'id': 'bo'}, model['users'], cfg)
        self.assertNotEqual(memory_path('/memory', 'ana'), memory_path('/memory', 'bo'))
        with self.assertRaises(ValueError):
            memory_path('/memory', '../bo')

    def test_model_and_tools_fail_closed(self):
        _, cfg = self.config()
        for field, value in [('base_url', 'https://external.example.org'),
                             ('base_url', 'http://litellm.external.example/other.svc:4000/v1'),
                             ('read_tools', ['shell']),
                             ('mcp_command', ['sh']), ('platform', 'unknown')]:
            bad = dict(cfg, **{field: value})
            with self.assertRaises(ValueError):
                validate(bad)
        validate(dict(cfg, platform='slack'))

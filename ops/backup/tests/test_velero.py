import importlib.util
from pathlib import Path
import unittest
import yaml

from tests.test_templates import fixture, subst

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('velero_check', ROOT / 'velero/check_resource_policy.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class VeleroTests(unittest.TestCase):
    def test_login_class_and_host_volumes_are_skipped(self):
        _, keys = fixture()
        policy = yaml.safe_load(subst((ROOT / 'velero/login-resource-policy.tmpl.yaml').read_text(), keys))
        login = keys['LOGIN_HOST_ROOT']
        storage = keys['STORAGE_CLASS_LOGIN']
        volume = {'metadata': {'labels': {'velero.io/exclude-from-backup': 'true'}},
                  'spec': {'local': {'path': login + '/ana'}, 'storageClassName': storage}}
        checker.validate(policy, [volume], login, [login], storage)
        volume['metadata']['labels'] = {}
        with self.assertRaises(ValueError):
            checker.validate(policy, [volume], login, [login], storage)

    def test_missing_mandatory_login_exclusion_refused(self):
        _, keys = fixture()
        policy = yaml.safe_load(subst((ROOT / 'velero/login-resource-policy.tmpl.yaml').read_text(), keys))
        with self.assertRaises(ValueError):
            checker.validate(policy, [], keys['LOGIN_HOST_ROOT'], [], keys['STORAGE_CLASS_LOGIN'])

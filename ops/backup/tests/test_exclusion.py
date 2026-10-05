import json
import unittest
from unittest.mock import patch
from backup import exclusions, main
from pathlib import Path
import shlex
from tests.test_templates import fixture, subst


class ExclusionTests(unittest.TestCase):
    def setUp(self):
        self.env = {'BACKUP_KIND': 'restic-sftp', 'BACKUP_LOGIN_ROOT': '/logins',
                    'BACKUP_EXCLUDE_JSON': '["/logins", "/excluded"]',
                    'BACKUP_SSH_CONFIG': '/ssh-config', 'BACKUP_REPOSITORY_PATH': 'repository'}

    def test_every_required_path_must_be_excluded(self):
        for active in [[], ['/logins'], ['/excluded'], ['/logins/**', '/excluded']]:
            with self.assertRaises(ValueError):
                exclusions(self.env, active)
        self.assertEqual(exclusions(self.env, ['/logins', '/excluded']), ['/logins', '/excluded'])

    def test_policy_requires_login_root(self):
        self.env['BACKUP_EXCLUDE_JSON'] = '["/excluded"]'
        with self.assertRaises(ValueError):
            exclusions(self.env, ['/excluded'])

    def test_preview_and_refusal_never_invoke_restic(self):
        with patch.dict('os.environ', self.env, clear=True), patch('subprocess.run') as run:
            self.assertEqual(main(['run']), 0)
            self.assertEqual(main(['run', '--yes', '--exclude', '/logins']), 1)
            run.assert_not_called()

    def test_malformed_exclusion_policy_refused(self):
        for policy in ['{}', '[]', '["relative"]', '[1]', 'null']:
            self.env['BACKUP_EXCLUDE_JSON'] = policy
            with self.assertRaises((ValueError, TypeError)):
                exclusions(self.env, [])

    def test_rendered_env_and_ssh_config(self):
        _, keys = fixture()
        root = Path(__file__).parents[1]
        rendered = subst((root / 'backup.tmpl.env').read_text(), keys)
        env = {}
        for line in rendered.splitlines():
            name, value = line.split('=', 1)
            env[name] = shlex.split(value)[0]
        self.assertEqual(env['BACKUP_LOGIN_ROOT'], keys['LOGIN_HOST_ROOT'])
        active = json.loads(env['BACKUP_EXCLUDE_JSON'])
        self.assertEqual(exclusions(env, active), active)
        ssh = subst((root / 'ssh_config.tmpl.conf').read_text(), keys)
        self.assertIn('StrictHostKeyChecking yes', ssh)
        self.assertIn('HostName ' + keys['BACKUP_SFTP_HOST'], ssh)

    def test_default_weekly_check_does_not_write_repository_locks(self):
        with patch.dict('os.environ', self.env, clear=True), patch('subprocess.run') as run:
            self.assertEqual(main(['check']), 0)
            self.assertIn('--no-lock', run.call_args.args[0])
            self.assertIn('--no-cache', run.call_args.args[0])

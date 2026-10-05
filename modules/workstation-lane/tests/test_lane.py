import datetime as dt
import json
import subprocess
import unittest
from unittest.mock import patch
from lane import in_window, local_now, transport


class LaneTests(unittest.TestCase):
    def test_overnight_window_and_drain(self):
        for hour, minute, expected in [(18, 59, False), (19, 0, True), (7, 44, True),
                                       (7, 45, False), (8, 0, False)]:
            self.assertEqual(in_window(dt.datetime(2025, 1, 1, hour, minute), '19:00', '08:00', 15), expected)

    def test_daytime_and_invalid_window(self):
        self.assertTrue(in_window(dt.datetime(2025, 1, 1, 12), '09:00', '17:00'))
        with self.assertRaises(ValueError):
            in_window(dt.datetime.now(), '08:00', '08:00')
        self.assertEqual(local_now('UTC').utcoffset(), dt.timedelta(0))

    def test_admin_kubeconfig_refused(self):
        config = {'kubeconfig': 'device', 'service_account': 'device-a', 'factory_pod': 'worker'}
        with patch('subprocess.check_output', return_value=json.dumps({'status': {'userInfo': {'username': 'admin'}}}).encode()):
            with self.assertRaises(PermissionError):
                transport(config, {'namespace': 'factory'}, ['pull'])

    def test_transport_device_identity_and_namespace(self):
        config = {'kubeconfig': 'device', 'service_account': 'device-a', 'factory_pod': 'worker'}
        who = {'status': {'userInfo': {'username': 'system:serviceaccount:factory:device-a'}}}
        with patch('subprocess.check_output', return_value=json.dumps(who).encode()), patch('subprocess.run',
                   return_value=subprocess.CompletedProcess([], 1, 'no', '')):
            command = transport(config, {'namespace': 'factory'}, ['pull'])
            self.assertIn('device', command)
            self.assertEqual(command[-4:], ['exec', 'worker', '--', 'pull'])

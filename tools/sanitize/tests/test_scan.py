import contextlib
import hashlib
import hmac
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import scan
import build_denylist
from atoms import candidates

class ScanTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.policy = json.loads((scan.HERE / 'rules.json').read_text())
        salt = 'ab' * 32
        self.deny = {'salt': salt, 'entries': [
            {'h': hmac.new(bytes.fromhex(salt), atom.encode(), hashlib.sha256).hexdigest(),
             'label': f'pii-{index:02d}', 'severity': 'fail'}
            for index, atom in enumerate(['zzownerx', '198.18.7.7', 'made up owner', 'made up'], 1)]}

    def write(self, path, text):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding='utf-8')
        return file

    def findings(self, allow=(), warn=False, export=False):
        return scan.scan(self.root, self.policy, self.deny, allow, warn, export)

    def test_candidate_contract(self):
        values = set(candidates('Prefix-ZZOWNERX.test 198.18.7.7 made up owner'))
        self.assertTrue({'zzownerx', '198.18.7.7', 'made up owner', 'made up'} <= values)
        self.assertNotIn('ninth', set(candidates('a.b.c.d.e.f.g.h.ninth')))

    def test_hash_hits_case_subtokens_and_ngrams(self):
        self.write('sample.txt', 'ZZOWNERX prefix-zzownerx.test\n198.18.7.7 made up owner')
        self.assertEqual(4, len(self.findings()))

    def test_windows_paths(self):
        self.write('sample.txt', 'C:' + '\\Users\\' + 'zzownerx\\file')
        self.assertIn('windows-user-home', {f['label'] for f in self.findings()})

    def test_pem_body_vs_header_only(self):
        self.write('header.txt', '-----BEGIN CERTIFICATE-----')
        self.assertEqual([], self.findings())
        self.write('body.txt', '-----BEGIN CERTIFICATE-----\n' + 'A' * 60)
        self.assertIn('pem-body', {f['label'] for f in self.findings()})

    def test_sealed_ciphertext(self):
        self.write('short.sealed.yaml', 'encryptedData:\n  value: placeholder')
        self.assertEqual([], self.findings(export=True))
        self.write('long.sealed.yaml', 'encryptedData:\n  value: ' + 'B' * 110)
        self.assertTrue(any(f['label'].startswith('forbidden:') for f in self.findings(export=True)))
        self.assertEqual([], self.findings())
        self.write('cipher.txt', 'Ag' + 'C' * 151)
        self.assertIn('sealed-ciphertext', {f['label'] for f in self.findings(export=True)})

    def test_forbidden_paths(self):
        paths = ['pub-cert.pem', 'a/cluster.env', 'secrets.env', 'LIVE-REPORT-test.md',
                 'a/proof/result.json', 'a-proof.json', 'alerts-before.json', 'alerts-after.json',
                 'previous/config.rev2.yaml', '__pycache__/cache.py']
        for path in paths:
            self.write(path, 'plain')
        self.assertEqual(len(paths), len(self.findings()))

    def test_allow_and_skips(self):
        self.write('tools/sanitize/tests/fixtures/secret.txt', 'zzownerx')
        for directory in scan.SKIP:
            self.write(directory + '/sample.txt', 'zzownerx')
        self.assertEqual([], self.findings(['tools/sanitize/tests/fixtures/**']))

    def test_binary_handling(self):
        self.write('sample.bin', 'zzownerx\0')
        self.write('docs/image.png', 'zzownerx\0')
        findings = self.findings()
        self.assertEqual(['forbidden:binary'], [f['label'] for f in findings])

    def test_cli_exit_and_neutral_output(self):
        config = self.root / 'config'
        config.mkdir()
        (config / 'rules.json').write_text(json.dumps(self.policy))
        (config / 'denylist.json').write_text(json.dumps(self.deny))
        (config / 'allow.txt').write_text('config/**\n')
        with patch.object(scan, 'HERE', config):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(0, scan.main(['--root', str(self.root)]))
            self.write('sample.txt', 'zzownerx')
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(1, scan.main(['--root', str(self.root), '--json']))
            self.assertNotIn('zzownerx', output.getvalue())
            self.assertEqual(1, json.loads(output.getvalue())['summary']['fail'])

    def test_warn_as_fail(self):
        self.write('sample.txt', '/home/' + 'zzperson/' + 'file')
        self.assertEqual('WARN', self.findings()[0]['level'])
        self.assertEqual('FAIL', self.findings(warn=True)[0]['level'])

    def test_builder_deduplication_and_no_cleartext(self):
        policy = {'rules': [{'severity': 'fail', 'kind': 'pii', 'atoms': ['ZZOWNERX', 'zzownerx']},
                            {'severity': 'warn', 'kind': 'pii', 'atoms': ['unused']}],
                  'extra_atoms': ['198.18.7.7']}
        built = build_denylist.build(policy, 'ab' * 32)
        serialized = json.dumps(built)
        self.assertNotIn('zzownerx', serialized.lower())
        self.assertNotIn('198.18.7.7', serialized)
        self.assertEqual(2 + len(build_denylist.EXTRAS), len(built['entries']))

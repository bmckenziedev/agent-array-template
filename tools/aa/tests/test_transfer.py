"""Synthetic regression coverage for committed uploads and archive boundaries."""
import io
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from aa_cli import transfer


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True,
                          capture_output=True).stdout


def tar(entries):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode='w') as archive:
        for name, kind, data in entries:
            item = tarfile.TarInfo(name)
            item.type = kind
            item.size = len(data) if kind == tarfile.REGTYPE else 0
            item.linkname = '../outside'
            archive.addfile(item, io.BytesIO(data) if item.isfile() else None)
    return result.getvalue()


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'repo'
        self.root.mkdir()
        git(self.root, 'init', '-q')
        git(self.root, 'config', 'user.name', 'Test User')
        git(self.root, 'config', 'user.email', 'test@example.org')
        git(self.root, 'config', 'core.autocrlf', 'false')
        git(self.root, 'remote', 'add', 'origin', 'git@github.com:example-org/payments-api.git')
        self.policy = {'estates': [{'id': 'payments', 'data_class': 'confidential',
                                   'repos': ['github.com/example-org/payments-api'],
                                   'snapshot_targets': ['claude', 'codex'],
                                   'deny_globs': ['private/**']}],
                       'vendors_allowed': {'confidential': ['anthropic']}}
        self.commit({'src/app.py': 'print(1)\n'})

    def commit(self, files):
        for name, data in files.items():
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('w', encoding='utf-8', newline='\n') as stream:
                stream.write(data)
        git(self.root, 'add', '.')
        git(self.root, 'commit', '-qm', 'synthetic files')

    def estate(self):
        return transfer.authorize(self.policy, 'payments', self.root, 'claude', 'anthropic')

    def names(self, data):
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            return {m.name: archive.extractfile(m).read() for m in archive if m.isfile()}

    def test_origin_normalization(self):
        expected = 'github.com/example-org/payments-api'
        for origin in ['git@github.com:example-org/payments-api.git',
                       'https://github.com/example-org/payments-api.git',
                       'ssh://git@github.com/example-org/payments-api', expected]:
            self.assertEqual(transfer.normalize_origin(origin), expected)

    def test_missing_policy_refused(self):
        with self.assertRaises(transfer.TransferError):
            transfer.authorize([], 'payments', self.root, 'claude', 'anthropic')

    def test_other_repo_refused(self):
        git(self.root, 'remote', 'set-url', 'origin', 'https://github.com/example-org/other')
        with self.assertRaises(transfer.TransferError):
            self.estate()

    def test_vendor_data_class_refused(self):
        with self.assertRaisesRegex(transfer.TransferError, 'data class'):
            transfer.authorize(self.policy, 'payments', self.root, 'codex', 'openai')

    def test_tool_target_refused(self):
        with self.assertRaisesRegex(transfer.TransferError, 'target'):
            transfer.authorize(self.policy, 'payments', self.root, 'kimi', 'moonshot')

    def test_vendor_mapping_missing_refused(self):
        del self.policy['vendors_allowed']
        with self.assertRaises(transfer.TransferError):
            self.estate()

    def test_repository_deny_refused(self):
        self.policy['estates'][0]['deny_globs'] = ['*payments-api*']
        with self.assertRaisesRegex(transfer.TransferError, 'deny'):
            self.estate()

    def test_head_only_and_stripping(self):
        self.commit({'.claude/rules.md': 'local', '.codex/config.toml': 'local',
                     '.kimi-code/a': 'local', '.agents/a': 'local', '.mcp.json': '{}',
                     '.env': 'local', 'credentials.json': '{}', 'private/data.txt': 'local',
                     'src/helper.py': 'help()\n'})
        (self.root / 'src/app.py').write_text('uncommitted', encoding='utf-8')
        (self.root / 'new.txt').write_text('untracked', encoding='utf-8')
        result = self.names(transfer.archive(self.root, self.estate()))
        self.assertEqual(result, {'src/app.py': b'print(1)\n', 'src/helper.py': b'help()\n'})

    def test_secret_fail_closed(self):
        self.commit({'src/leak.py': 'key = "' + 'sk-' + 'aB2cD3eF4gH5iJ6kL7mN8oP9' + '"'})
        with self.assertRaisesRegex(transfer.TransferError, 'secret'):
            transfer.archive(self.root, self.estate())

    def test_credential_assignment_fail_closed(self):
        self.commit({'src/leak.py': 'password = "aB2cD3eF4gH5iJ6kL7mN8oP9"'})
        with self.assertRaisesRegex(transfer.TransferError, 'credential'):
            transfer.archive(self.root, self.estate())

    def test_remote_change_after_authorization_refused(self):
        estate = self.estate()
        git(self.root, 'remote', 'set-url', 'origin', 'https://github.com/example-org/other')
        with self.assertRaises(transfer.TransferError):
            transfer.archive(self.root, estate)

    def test_git_symlink_refused(self):
        blob = subprocess.run(['git', '-C', str(self.root), 'hash-object', '-w', '--stdin'],
                              input=b'outside', check=True, capture_output=True).stdout.decode().strip()
        git(self.root, 'update-index', '--add', '--cacheinfo', '120000,' + blob + ',link')
        git(self.root, 'commit', '-qm', 'synthetic link')
        with self.assertRaisesRegex(transfer.TransferError, 'links'):
            transfer.archive(self.root, self.estate())

    def test_safe_extract(self):
        out = Path(self.tmp.name) / 'export'
        transfer.extract_export(tar([('src/file.py', tarfile.REGTYPE, b'print(1)')]), out)
        self.assertEqual((out / 'src/file.py').read_bytes(), b'print(1)')

    def test_unsafe_paths_refused_before_writes(self):
        for name in ['../outside', '/absolute', 'C:/x', 'a\\b', 'a/./b', 'a//b',
                     'NUL.txt', 'aux', 'com1.log', 'trailing.', 'a/b ', 'a:b']:
            with self.subTest(name=name):
                out = Path(self.tmp.name) / 'export'
                data = tar([('safe', tarfile.REGTYPE, b'ok'), (name, tarfile.REGTYPE, b'bad')])
                with self.assertRaises(transfer.TransferError):
                    transfer.extract_export(data, out)
                self.assertFalse(out.exists())

    def test_archive_links_refused(self):
        for kind in [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE]:
            with self.subTest(kind=kind), self.assertRaises(transfer.TransferError):
                transfer.extract_export(tar([('link', kind, b'')]), Path(self.tmp.name) / 'export')

    def test_duplicate_case_and_parent_collisions(self):
        for names in [('A', 'a'), ('a', 'a'), ('dir', 'dir/a'), ('Dir/a', 'dir/b')]:
            with self.subTest(names=names), self.assertRaises(transfer.TransferError):
                transfer.extract_export(tar([(n, tarfile.REGTYPE, b'x') for n in names]),
                                        Path(self.tmp.name) / 'export')

    def test_nonempty_destination_refused(self):
        with self.assertRaises(transfer.TransferError):
            transfer.extract_export(tar([('new', tarfile.REGTYPE, b'x')]), self.root)

    def test_invalid_tar_refused(self):
        with self.assertRaises(transfer.TransferError):
            transfer.extract_export(b'invalid', Path(self.tmp.name) / 'export')

    def test_expanded_size_bound(self):
        data = tar([('big', tarfile.REGTYPE, b'x' * (transfer.MAX_FILE + 1))])
        with self.assertRaisesRegex(transfer.TransferError, 'limit'):
            transfer.extract_export(data, Path(self.tmp.name) / 'export')


if __name__ == '__main__':
    unittest.main()

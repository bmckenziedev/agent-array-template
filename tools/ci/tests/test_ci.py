import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_tests
import linkcheck
import lint_cel

class TreeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write(self, path, text=''):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding='utf-8')
        return file

    def test_discovery_excludes_live_fixtures_and_venvs(self):
        self.write('component/tests/test_good.py')
        self.write('component/tests/test-shell.sh')
        self.write('component/tests/live/tests/test_bad.py')
        self.write('fixtures/x/tests/test_bad.py')
        self.write('.ci-venvs/x/tests/test_bad.py')
        self.assertEqual(['component', 'component/tests/test-shell.sh'],
                         [s[0] for s in run_tests.discover(self.root)])

    def test_discovery_shell_only_and_nested_tests(self):
        self.write('a/tests/test-smoke.sh')
        self.write('b/tests/nested/test_example.py')
        self.assertEqual(2, len(run_tests.discover(self.root)))

    def test_interpreter_selection(self):
        tests = self.root / 'a/tests'
        with patch('run_tests.subprocess.run') as run:
            run.return_value.returncode = 0
            self.assertIn('pytest', run_tests.python_command('python', tests))
            run.return_value.returncode = 1
            command = run_tests.python_command('python', tests)
            self.assertIn('unittest', command[2])
            self.assertEqual(".", command[-1])

    def test_only_listing_and_failure_exit(self):
        suites = [('a', 'python', self.root / 'a/tests'), ('b', 'python', self.root / 'b/tests')]
        with patch('run_tests.discover', return_value=suites), patch('run_tests.execute', return_value=('FAIL', '')):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(0, run_tests.main(['--list', '--only', 'a']))
                self.assertEqual('a\tpython\n', output.getvalue())
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(1, run_tests.main(['--only', 'a']))

    def test_unittest_execution_never_collects_live_packages(self):
        self.write('a/tests/__init__.py')
        self.write('a/tests/test_safe.py', 'import unittest\nclass Safe(unittest.TestCase):\n def test_ok(self): pass\n')
        self.write('a/tests/live/__init__.py')
        self.write('a/tests/live/check_unsafe.py', 'raise RuntimeError("live check was collected")')
        import subprocess
        with patch('run_tests.subprocess.run') as mocked:
            mocked.return_value.returncode = 1
            command = run_tests.python_command(sys.executable, self.root / 'a/tests')
        result = subprocess.run(command, cwd=self.root / "a", capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('Ran 1 test', result.stderr)

    def test_github_slugs(self):
        self.assertEqual({'hello-world', 'hello-world-1', 'inline-code', 'under_score', 'setext'},
                         linkcheck.anchors('# Hello, world!\n# Hello, world!\n## `Inline` code\n# under_score\nSetext\n------'))

    def test_links_and_anchors(self):
        self.write('target.md', '# Hello, world!\n# Hello, world!')
        self.write('README.md', '[ok](target.md#hello-world-1)\n[bad](target.md#missing)\n[absent](missing.md)')
        self.assertEqual({'missing-anchor', 'missing-target'}, {e[2] for e in linkcheck.check(self.root)})

    def test_reference_links_external_and_code(self):
        self.write('target.md', '# Heading')
        self.write('README.md', '[ok][ref]\n\n[ref]: target.md#heading\n[web](https://example.org)\n'
                   '```md\n[ignored](missing.md)\n```\n`[ignored](missing.md)`')
        self.assertEqual([], linkcheck.check(self.root))

    def test_personal_paths_and_outside_tree(self):
        windows = 'C:' + '\\Users\\' + 'example\\source'
        self.write('README.md', '[bad](' + windows + ')\n[bad](/c/' + 'Users/example/source)\n[bad](../source.md)')
        self.assertEqual({'personal-path', 'outside-repository'}, {e[2] for e in linkcheck.check(self.root)})

    def test_source_url_detection_uses_neutral_labels(self):
        self.write('README.md', '[source](https://example.org/zzownerx/source)')
        with patch('linkcheck.identifier_checker', return_value=lambda value: 'zzownerx' in value):
            errors = linkcheck.check(self.root)
        self.assertEqual('source-identifier', errors[0][2])
        self.assertNotIn('zzownerx', str(errors))

    def test_cel_good_and_bad(self):
        self.assertEqual(['object.spec.initContainers'], lint_cel.reads('object.spec.initContainers.all(c, true)'))
        self.assertEqual([], lint_cel.reads('!has(object.spec.initContainers) || object.spec.initContainers.size() == 0'))
        self.assertEqual([], lint_cel.reads('object.spec?.initContainers.orValue([]).size() == 0'))
        self.assertEqual([], lint_cel.reads('variables.present && object.spec.volumes.size() == 0',
                                          {'present': 'has(object.spec.volumes)'}))
        self.assertEqual(['c.securityContext', 'c.securityContext.privileged'], lint_cel.reads('has(object.spec.securityContext) && c.securityContext.privileged'))

    def test_policies_all_expression_locations(self):
        policy = {'apiVersion': 'admissionregistration.k8s.io/v1', 'kind': 'ValidatingAdmissionPolicy',
                  'spec': {'validations': [{'expression': 'object.spec.hostNetwork == false'}],
                           'matchConditions': [{'expression': 'has(object.spec.volumes)'}],
                           'variables': [{'name': 'safe', 'expression': 'has(object.spec.env)'}],
                           'auditAnnotations': [{'key': 'test', 'valueExpression': 'object.spec.serviceAccountName'}]}}
        self.write('policy.yaml', yaml.safe_dump(policy))
        self.assertEqual(2, len(lint_cel.check(self.root)))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(1, lint_cel.main([str(self.root)]))
        policy['spec']['validations'][0]['expression'] = 'has(object.spec.hostNetwork) && object.spec.hostNetwork == false'
        policy['spec']['auditAnnotations'][0]['valueExpression'] = 'object.spec?.serviceAccountName.orValue("")'
        self.write('policy.yaml', yaml.safe_dump(policy))
        self.assertEqual([], lint_cel.check(self.root))

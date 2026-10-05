import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which('bash')


@unittest.skipUnless(BASH, 'Bash unavailable')
class WindowTests(unittest.TestCase):
    def test_every_entrypoint_previews_without_commands(self):
        for script in sorted(ROOT.glob('[0-6][0-9]-*.sh')):
            # Missing node configuration proves mutation paths are never entered.
            run = subprocess.run([BASH, script.as_posix()], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('Preview only', run.stdout)

    def test_window_flag_requires_node_configuration(self):
        run = subprocess.run([BASH, (ROOT / '40-node-maint.sh').as_posix(), '--i-am-in-the-window'],
                             capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)

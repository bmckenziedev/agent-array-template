"""Disposable test state and engine imports."""
from pathlib import Path
import sys
import tempfile
ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE))
class TempDir:
    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        return Path(self.tmp.name)
    def __exit__(self, *args):
        self.tmp.cleanup()

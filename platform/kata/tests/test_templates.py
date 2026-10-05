"""RuntimeClass template contract against the canonical organization fixture."""
import json
from pathlib import Path
import re
import unittest
import yaml

HERE = Path(__file__).resolve().parent


class RuntimeTemplate(unittest.TestCase):
    def test_runtimeclass(self):
        keys = json.loads((HERE / "fixtures/org.fixture.json").read_text())["keys"]
        text = (HERE.parent / "k8s/runtimeclass.tmpl.yaml").read_text()
        def replace(match):
            if match.group(1) not in keys:
                raise KeyError(match.group(1))
            return keys[match.group(1)]
        obj = yaml.safe_load(re.sub(r"\{\{([A-Z][A-Z0-9_]*)\}\}", replace, text))
        self.assertEqual(obj["handler"], "kata")
        self.assertEqual(obj["metadata"]["name"], keys["RUNTIME_CLASS_VM"])
        self.assertEqual(obj["scheduling"]["nodeSelector"], {keys["LABEL_PREFIX"] + "/runtime-kata": "true"})

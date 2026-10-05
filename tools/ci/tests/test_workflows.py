"""Hosted workflows must exercise builds without implicit registry writes."""
from pathlib import Path
import unittest
import tempfile
import sys

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'tools/ci'))
sys.path.insert(0, str(ROOT / 'tools/render'))
from module_config import create
from aa_render.yamlsub import load
from aa_render.model import load_model


class WorkflowTests(unittest.TestCase):
    def test_synthetic_strict_fixture_has_no_artifact_or_node_warnings(self):
        with tempfile.TemporaryDirectory() as scratch:
            target = Path(scratch)
            create(ROOT / 'org/org.example.yaml', target, strict_fixture=True)
            model = load_model(ROOT, target / 'org.example.yaml', strict=True)
            self.assertEqual(model['warnings'], [])

    def test_all_module_config_remains_in_strict_yaml_subset(self):
        with tempfile.TemporaryDirectory() as scratch:
            target = Path(scratch)
            model = create(ROOT / 'org/org.example.yaml', target)
            parsed = load(target / 'org.example.yaml')
            self.assertEqual(parsed, model)
            self.assertTrue(all(m['enabled'] for m in parsed['modules'].values()))
            self.assertIsInstance(parsed['alerting']['quiet_hours']['start'], str)
            self.assertTrue(all(lane['gguf_sha256'] != 'REPLACE_WITH_VERIFIED_SHA256'
                                for lane in parsed['modules']['gpu-lanes']['lanes']))

    def test_all_ten_images_build_and_publishing_requires_configuration(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/images.yml').read_text())
        build = workflow['jobs']['build']
        self.assertEqual(len(build['strategy']['matrix']['include']), 10)
        for image in build['strategy']['matrix']['include']:
            self.assertTrue((ROOT / image['file']).is_file())
        condition = build['env']['PUBLISH_IMAGES']
        for key in ('vars.PUBLISH_IMAGES', 'vars.REGISTRY', 'vars.IMAGE_PREFIX'):
            self.assertIn(key, condition)
        step = next(s for s in build['steps'] if s.get('id') == 'build')
        self.assertEqual(step['with']['push'], "${{ env.PUBLISH_IMAGES == 'true' }}")
        self.assertEqual(step['if'], "steps.present.outputs.exists == 'true'")

    def test_unit_job_installs_production_javascript_gate_dependencies(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())
        steps = workflow['jobs']['unit']['steps']
        commands = [s.get('run') for s in steps]
        install = 'npm ci --ignore-scripts --prefix modules/factory/engine/js'
        self.assertLess(commands.index(install), commands.index('python tools/ci/run_tests.py'))
        self.assertTrue(any(s.get('uses') == 'actions/setup-node@v4' for s in steps))

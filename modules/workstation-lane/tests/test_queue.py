import base64
from pathlib import Path
import tempfile
import unittest

from queue_adapter import publish, resume, safe_path, stage


class QueueTests(unittest.TestCase):
    def test_path_traversal_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('../escape', '/absolute', 'C:/escape', 'a\\b', '.'):
                with self.assertRaises(ValueError):
                    safe_path(Path(tmp), name)

    def test_durable_lease_resumes_until_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = {'batch': 'a' * 64, 'cards': [{'kind': 'doc_map'}],
                    'files': {'repo/file.txt': base64.b64encode(b'synthetic input').decode()}}
            task = stage(root, data)
            self.assertEqual(resume(root), task)
            self.assertFalse(publish(task, lambda value: {'published': True}, [{'status': 'running'}]))
            self.assertFalse(publish(task, lambda value: {'published': False}, [{'status': 'complete'}]))
            self.assertTrue(publish(task, lambda value: {'published': True}, [{'status': 'complete'}]))
            self.assertIsNone(resume(root))

    def test_unknown_tasks_and_batch_ids_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            for data in [{'batch': '../bad', 'cards': [], 'files': {}},
                         {'batch': 'a' * 64, 'cards': [{'kind': 'shell'}], 'files': {}}]:
                with self.assertRaises(ValueError):
                    stage(Path(tmp), data)

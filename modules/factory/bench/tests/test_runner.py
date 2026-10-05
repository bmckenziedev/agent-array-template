import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from harness.runner import run, prompt


class RunnerTest(unittest.TestCase):
    def test_bounded_source_context_and_private_truth(self):
        captured = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                captured.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                response = json.dumps({"choices": [{"message": {"content": "candidate"}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, *args):
                pass

        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            source = "function example(value) { return value + 1; }\n"
            (root / "sample.cjs").write_text(source, encoding="utf-8", newline="\n")
            unit = {"id": "unit", "kind": "doc_map", "target": "sample.cjs",
                    "exports": ["example"], "repo_root": str(root),
                    "reference": "private-reference", "mutation": ["private-mutant"]}
            server = HTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                with patch("harness.runner.gate", return_value="pass"):
                    run([unit], {"id": "fake", "endpoint": f"http://127.0.0.1:{server.server_port}"}, root / "results.jsonl")
            finally:
                server.shutdown()
                thread.join()
                server.server_close()
            content = captured[0]["messages"][0]["content"]
            self.assertIn(source, content)
            self.assertIn("UNTRUSTED REPOSITORY DATA", content)
            for private in ("private-reference", "private-mutant", str(root)):
                self.assertNotIn(private, content)
            results = (root / "results.jsonl").read_text()
            self.assertNotIn(source.strip(), results)
            self.assertNotIn("messages", results)
            unit["source_sha256"] = "incorrect"
            with self.assertRaisesRegex(ValueError, "snapshot changed"):
                prompt(unit)

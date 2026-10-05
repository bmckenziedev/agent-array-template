"""Projected-token rotation across requests to an in-process MCP server."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest


class BridgeTests(unittest.TestCase):
    def test_rotated_token_and_session(self):
        loader = importlib.machinery.SourceFileLoader("bridge", str(
            Path(__file__).resolve().parents[1] / "common/bin/aa-mcp-bridge"))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        bridge = importlib.util.module_from_spec(spec)
        loader.exec_module(bridge)
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append((self.headers["Authorization"], self.headers.get("Mcp-Session-Id")))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Mcp-Session-Id", "test-session")
                self.end_headers()
                self.wfile.write(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {}}).encode())

        with tempfile.TemporaryDirectory() as temporary:
            token = Path(temporary) / "token"
            with HTTPServer(("127.0.0.1", 0), Handler) as server:
                thread = threading.Thread(target=server.serve_forever)
                thread.start()
                try:
                    client = bridge.Bridge(f"http://127.0.0.1:{server.server_port}/mcp", token)
                    for i in range(2):
                        token.write_text(f"synthetic-{i}")
                        self.assertEqual(client.request({"jsonrpc": "2.0", "id": i, "method": "tools/list"})[0]["id"], i)
                    self.assertEqual(seen, [("Bearer synthetic-0", None), ("Bearer synthetic-1", "test-session")])
                    with self.assertRaises(ValueError):
                        client.request({"jsonrpc": "2.0", "id": 3, "params": "x" * bridge.LIMIT})
                finally:
                    server.shutdown()
                    thread.join()

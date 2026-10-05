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
        protocols = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append((self.headers["Authorization"], self.headers.get("Mcp-Session-Id")))
                protocols.append(self.headers.get("MCP-Protocol-Version"))
                self.send_response(200)
                streaming = request.get("method") == "initialize"
                self.send_header("Content-Type", "text/event-stream" if streaming else "application/json")
                self.send_header("Mcp-Session-Id", "test-session")
                self.end_headers()
                result = {"jsonrpc": "2.0", "id": request["id"], "result": {"protocolVersion": "2025-06-18"} if streaming else {}}
                body = json.dumps(result)
                self.wfile.write(("data: " + body + "\n\n" if streaming else body).encode())

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
                    client.request({"jsonrpc": "2.0", "id": 5, "method": "initialize"})
                    self.assertEqual(client.protocol, "2025-06-18")
                    client.request({"jsonrpc": "2.0", "id": 6, "method": "tools/list"})
                    self.assertEqual(protocols[-1], "2025-06-18")
                    with self.assertRaises(ValueError):
                        client.request({"jsonrpc": "2.0", "id": 3, "params": "x" * bridge.LIMIT})
                finally:
                    server.shutdown()
                    thread.join()

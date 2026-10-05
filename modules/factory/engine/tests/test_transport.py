"""New synthetic HTTP endpoints exercise real stdlib model transports."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from factory_engine import client, config


@contextmanager
def endpoint(mode="marker", require_key=False, strict=False):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, value):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(value).encode())

        def do_GET(self):
            if self.path == "/health":
                self.reply(200, {"status": "ok"})
            elif require_key and self.headers.get("Authorization") != "Bearer synthetic-key":
                self.reply(401, {"error": "unauthorized"})
            else:
                self.reply(200, {"data": [{"id": "synthetic-model"}]})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, body, self.headers.get("Authorization")))
            if require_key and self.headers.get("Authorization") != "Bearer synthetic-key":
                self.reply(401, {"error": "unauthorized"})
                return
            if mode == "failure":
                self.reply(503, {"error": "synthetic unavailability"})
                return
            if strict and "top_k" in body:
                self.reply(400, {"error": "unsupported sampling option"})
                return
            value = '{"normalizeScore":{"summary":"Clamp a synthetic score."}}'
            finish = "length" if mode == "length" else "stop"
            if self.path == "/completion":
                stop = {"marker": "word", "eos": "eos", "length": "limit"}[mode]
                self.reply(200, {"content": value, "stop_type": stop,
                                 "stopping_word": config.STOP if mode == "marker" else None,
                                 "tokens_evaluated": 12, "tokens_predicted": 7})
            elif self.path == "/v1/chat/completions":
                self.reply(200, {"choices": [{"message": {"content": "<<<CODE\n" + value + "\nCODE>>>"},
                                             "finish_reason": finish}],
                                 "usage": {"prompt_tokens": 12, "completion_tokens": 7}})
            else:
                text = value + (config.STOP if mode == "marker" else "")
                self.reply(200, {"choices": [{"text": text, "finish_reason": finish}],
                                 "usage": {"prompt_tokens": 12, "completion_tokens": 7}})
    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


class TransportTest(unittest.TestCase):
    def lane(self, base, api="openai-completions", **extra):
        return {"tier": "synthetic", "concurrency": 1, "ctx": 8192, "prompt_cap": 4096,
                "endpoint": {"base_url": base, "api": api, "model": "synthetic-model",
                             "timeout_s": 2, **extra}}

    def generate(self, lane):
        return client.generate(lane, "Document a synthetic function.",
                               "function normalizeScore(score) { return Math.max(0, score); }",
                               100, {"temperature": 0.2}, 41)

    def test_three_transports_accept_stops_and_reject_truncation(self):
        for api in ("openai-completions", "llamacpp", "openai-chat"):
            for mode in ("marker", "eos", "length"):
                with self.subTest(api=api, mode=mode), endpoint(mode) as (url, requests):
                    generated = self.generate(self.lane(url, api))
                    self.assertEqual(generated.envelope.ok, mode != "length")
                    self.assertEqual(generated.prompt_tokens, 12)
                    self.assertEqual(generated.completion_tokens, 7)
                    self.assertEqual(requests[0][1]["seed"], 41)
                    if mode != "length":
                        self.assertIn("normalizeScore", json.loads(generated.envelope.code))
                    else:
                        self.assertEqual(generated.stop, "length")

    def test_auth_env_and_file_never_returned(self):
        with endpoint(require_key=True) as (url, requests), tempfile.TemporaryDirectory() as temp:
            token = Path(temp) / "lane-key"
            token.write_text("synthetic-key", encoding="utf-8")
            with patch.dict(os.environ, {"SYNTHETIC_LANE_KEY": "synthetic-key"}):
                for key in ({"key_env": "SYNTHETIC_LANE_KEY"}, {"key_file": str(token)}):
                    lane = self.lane(url, **key)
                    result = self.generate(lane)
                    self.assertTrue(result.envelope.ok)
                    self.assertNotIn("synthetic-key", json.dumps(result.server))
                    self.assertTrue(client.probe(lane)["ok"])
                with self.assertRaises(client.InfraError) as raised:
                    self.generate(self.lane(url))
                self.assertNotIn("synthetic-key", str(raised.exception))
            self.assertEqual(requests[0][2], "Bearer synthetic-key")

    def test_strict_completion_endpoint_retries_without_extras(self):
        with endpoint(strict=True) as (url, requests):
            generated = self.generate(self.lane(url, accept_unknown_stop=True))
            self.assertTrue(generated.envelope.ok)
            self.assertEqual(len(requests), 2)
            self.assertIn("top_k", requests[0][1])
            self.assertNotIn("top_k", requests[1][1])

    def test_http_failure_is_infrastructure(self):
        with endpoint(mode="failure") as (url, _):
            with self.assertRaises(client.InfraError):
                self.generate(self.lane(url))

    def test_closed_endpoint_is_infrastructure(self):
        server = HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
        url = f"http://127.0.0.1:{server.server_port}"
        server.server_close()
        with self.assertRaises(client.InfraError):
            self.generate(self.lane(url))


if __name__ == "__main__":
    unittest.main()

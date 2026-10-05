#!/usr/bin/env python3
"""Deterministic local model stand-in for actual orchestrator/tester Docker runs."""

import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

KEY = os.environ["MODEL_TEST_KEY"]
SCENARIO = os.environ.get("MODEL_SCENARIO", "pass")
CODE = "def clamp(value, low, high):\n    if low > high:\n        raise ValueError('range')\n    return max(low, min(value, high))\n"
CHECK = "import os\nfrom core import clamp\nassert not any(k in os.environ for k in ('TASK_TOKEN','LITELLM_KEY','MODEL_TEST_KEY','LITELLM_MINT_KEY'))\nassert clamp(-1,0,10)==0\nassert clamp(20,0,10)==10\nprint('2 passed')\n"


class Model(BaseHTTPRequestHandler):
    writes = 0

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.headers.get("Authorization") != "Bearer " + KEY or body.get(
            "model"
        ) not in ["local-coder", "local-coder-small"]:
            self.send_error(403)
            return
        messages = body["messages"]
        system = messages[0]["content"]
        user = messages[-1]["content"]
        if "frame a coding task" in system:
            content = json.dumps(
                {
                    "intent": "Implement clamp with bounds",
                    "complexity": "trivial",
                    "acceptance_criteria": ["clamp honors both bounds"],
                }
            )
        elif "precise local coding model" in system:
            Model.writes += 1
            code = CODE
            if SCENARIO == "repair" and Model.writes == 1:
                code = code.replace("return max(low, min(value, high))", "return low")
            content = json.dumps(
                {
                    "files": [
                        {"path": "core.py", "content": code},
                        {"path": "check.py", "content": CHECK},
                    ],
                    "delete": [],
                    "notes": "local fixture",
                }
            )
        elif "review the code" in system:
            ok = "return max(low, min(value, high))" in user
            content = json.dumps(
                {"pass": ok, "issues": [] if ok else ["upper bound failed"]}
            )
        else:
            content = "Implement the fixture clamp function and check both bounds."
        response = {
            "id": "chatcmpl-fixture",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body["model"],
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": content},
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("x-litellm-model-group", body["model"])
        self.end_headers()
        self.wfile.write(json.dumps(response).encode())


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8080), Model).serve_forever()

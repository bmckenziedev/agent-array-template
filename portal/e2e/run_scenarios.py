#!/usr/bin/env python3
"""Offline panel-to-Job contract scenarios, executed only by the Docker harness."""

from __future__ import annotations
import asyncio
import contextlib
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.request

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from kubernetes import client, config

HERE = Path(__file__).resolve().parent
PANEL = HERE.parent / "panel"
if (PANEL / "app").exists():
    PANEL = PANEL / "app"
JOBS = HERE.parent / "session-jobs"
if not JOBS.exists():
    JOBS = HERE.parents[1] / "modules/session-jobs"
FIXTURE = json.loads((HERE / "org.fixture.json").read_text())


@contextlib.contextmanager
def server(handler, tls=None):
    http = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    if tls:
        http.socket = tls.wrap_socket(http.socket, server_side=True)
    thread = threading.Thread(target=http.serve_forever)
    thread.start()
    try:
        yield http
    finally:
        http.shutdown()
        thread.join(timeout=5)
        http.server_close()
        assert not thread.is_alive()


def archive(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as bundle:
        for name, text in files.items():
            body = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(body)
            bundle.addfile(info, io.BytesIO(body))
    return output.getvalue()


class Response:
    def __init__(self, status, content):
        self.status_code, self.content = status, content
        self.text = content.decode(errors="replace")

    def json(self):
        return json.loads(self.content)


class ASGIClient:
    def __init__(self, app, port):
        self.app, self.port = app, port

    def close(self):
        pass

    def request(
        self, method, path, headers=None, data=None, files=None, content=None, json=None
    ):
        import json as jsonlib

        headers = dict(headers or {})
        if json is not None:
            body = jsonlib.dumps(json).encode()
            headers["Content-Type"] = "application/json"
        elif data is not None or files:
            boundary = "e2e-" + secrets.token_hex(8)
            body = b""
            for name, value in (data or {}).items():
                body += (
                    "--"
                    + boundary
                    + '\r\nContent-Disposition: form-data; name="'
                    + name
                    + '"\r\n\r\n'
                    + value
                    + "\r\n"
                ).encode()
            for name, (filename, payload) in files or []:
                body += (
                    (
                        "--"
                        + boundary
                        + '\r\nContent-Disposition: form-data; name="'
                        + name
                        + '"; filename="'
                        + filename
                        + '"\r\nContent-Type: application/octet-stream\r\n\r\n'
                    ).encode()
                    + payload
                    + b"\r\n"
                )
            body += ("--" + boundary + "--\r\n").encode()
            headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
        else:
            body = content or b""
        headers["Content-Length"] = str(len(body))

        async def invoke():
            messages = []
            received = False

            async def receive():
                nonlocal received
                if not received:
                    received = True
                    return {"type": "http.request", "body": body, "more_body": False}
                await asyncio.sleep(3600)

            async def send(message):
                messages.append(message)

            scope = {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": method,
                "scheme": "http",
                "path": path,
                "raw_path": path.encode(),
                "query_string": b"",
                "root_path": "",
                "headers": [
                    (k.lower().encode(), v.encode()) for k, v in headers.items()
                ],
                "server": ("testserver", self.port),
                "client": ("127.0.0.1", 1),
            }
            await self.app(scope, receive, send)
            status = next(
                m["status"] for m in messages if m["type"] == "http.response.start"
            )
            return Response(
                status,
                b"".join(
                    m.get("body", b"")
                    for m in messages
                    if m["type"] == "http.response.body"
                ),
            )

        return asyncio.run(invoke())

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)


class Kubelet:
    def __init__(self):
        self.jobs = []

    def create_namespaced_job(self, namespace, body):
        self.jobs.append(body)

    def delete_namespaced_job(self, *args, **kwargs):
        return None

    def read_namespaced_job_status(self, *args, **kwargs):
        raise client.exceptions.ApiException(status=404)


class Scenarios(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = contextlib.ExitStack()
        cls.addClassCleanup(cls.stack.close)
        cls.temp = Path(
            cls.stack.enter_context(tempfile.TemporaryDirectory(prefix="portal-e2e-"))
        )
        cls.signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.kid = secrets.token_hex(8)
        cls.jwk = json.loads(
            jwt.algorithms.RSAAlgorithm.to_jwk(cls.signing_key.public_key())
        )
        cls.jwk.update(kid=cls.kid, use="sig", alg="RS256")
        certkey = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        now = datetime.now(timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(certkey.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(hours=1))
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
                ),
                critical=False,
            )
            .sign(certkey, hashes.SHA256())
        )
        certpath, keypath = cls.temp / "cert.pem", cls.temp / "cert-key.pem"
        certpath.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        keypath.write_bytes(
            certkey.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(certpath, keypath)

        class Issuer(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path == "/.well-known/openid-configuration":
                    body = {"issuer": cls.issuer, "jwks_uri": cls.issuer + "/jwks"}
                elif self.path == "/jwks":
                    body = {"keys": [cls.jwk]}
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

        issuer = cls.stack.enter_context(server(Issuer, tls))
        cls.issuer = "https://127.0.0.1:" + str(issuer.server_port)
        cls.requests, cls.model_calls, cls.task_keys = [], [], {}
        cls.mint_key = "sk-e2e-mint-" + secrets.token_hex(16)

        class LiteLLM(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                request = json.loads(
                    self.rfile.read(int(self.headers["Content-Length"]))
                )
                bearer = self.headers.get("Authorization", "").removeprefix("Bearer ")
                status = 200
                if self.path == "/key/generate" and bearer == cls.mint_key:
                    cls.requests.append(request)
                    key = "sk-e2e-task-" + secrets.token_hex(16)
                    cls.task_keys[key] = request
                    body = {
                        **request,
                        "key": key,
                        "token": hashlib.sha256(key.encode()).hexdigest(),
                        "allowed_routes": ["llm_api_routes"],
                        "expires": (
                            datetime.now(timezone.utc) + timedelta(seconds=3900)
                        ).isoformat(),
                    }
                elif self.path == "/key/delete" and bearer == cls.mint_key:
                    body = {"deleted_keys": request.get("keys", [])}
                elif self.path == "/v1/chat/completions" and bearer in cls.task_keys:
                    if request["model"] not in cls.task_keys[bearer]["models"]:
                        status, body = 403, {"error": "model denied"}
                    else:
                        cls.model_calls.append(request["model"])
                        body = {
                            "choices": [
                                {
                                    "message": {
                                        "content": "def clamp(value, low, high):\n    if low > high:\n        raise ValueError('range')\n    return max(low, min(value, high))\n"
                                    }
                                }
                            ]
                        }
                else:
                    status, body = 403, {"error": "route denied"}
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

        llm = cls.stack.enter_context(server(LiteLLM))
        cls.llm = "http://127.0.0.1:" + str(llm.server_port)
        directory = cls.temp / "org"
        directory.mkdir()
        cls.users = [dict(user) for user in FIXTURE["users"]]
        cls.users.append(
            {
                "slug": "silo",
                "oidc_sub": "e2e-platform-only",
                "status": "active",
                "teams": ["platform"],
                "primary_team": "platform",
            }
        )
        cls.teams = FIXTURE["teams"]
        for name, rows in [
            ("users", cls.users),
            ("teams", cls.teams),
            ("accounts", FIXTURE["accounts"]["accounts"]),
        ]:
            (directory / (name + ".json")).write_text(json.dumps(rows))
        keys = dict(FIXTURE["keys"])
        keys["M_SESSION_JOBS_RUNTIME"] = "kata"
        raw = (JOBS / "task-job.template.yaml").read_text()
        rendered = re.sub(r"\{\{([A-Z][A-Z0-9_]*)\}\}", lambda m: keys[m.group(1)], raw)
        template = cls.temp / "job.yaml"
        template.write_text(rendered)
        env = {
            "OIDC_ISSUER_URL": cls.issuer,
            "OIDC_CLIENT_ID": "e2e-panel",
            "GROUP_PLATFORM_ADMIN": "aa-platform-admin",
            "GROUP_AUDITOR": "aa-auditor",
            "PANEL_ORIGIN": "https://panel.example.org",
            "ACCESS_KIND": "oidc-proxy",
            "ORG_DIRECTORY": str(directory),
            "DATA_DIR": str(cls.temp / "data"),
            "SESSION_JOBS_ENABLED": "true",
            "SESSION_IMAGE": "example/session-runner:test",
            "LITELLM_ADMIN_BASE": cls.llm,
            "LITELLM_MINT_KEY": cls.mint_key,
            "JOB_TEMPLATE": str(template),
            "SSL_CERT_FILE": str(certpath),
        }
        cls.stack.enter_context(patch.dict(os.environ, env))
        cls.kubelet = Kubelet()
        cls.stack.enter_context(
            patch.object(config, "load_incluster_config", lambda: None)
        )
        cls.stack.enter_context(patch.object(client, "BatchV1Api", lambda: cls.kubelet))
        sys.path.insert(0, str(PANEL))
        spec = importlib.util.spec_from_file_location(
            "panel_e2e_main", PANEL / "main.py"
        )
        cls.panel = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.panel)
        cls.panel.TASKS.mkdir(parents=True)
        cls.public = ASGIClient(cls.panel.app, 8080)
        cls.internal = ASGIClient(cls.panel.app, 8081)
        cls.stack.callback(cls.public.close)
        cls.stack.callback(cls.internal.close)

    def headers(self, slug="ana", **claims):
        user = next(user for user in self.users if user["slug"] == slug)
        groups = [
            team["idp_group"] for team in self.teams if team["id"] in user["teams"]
        ]
        body = {
            "iss": self.issuer,
            "aud": "e2e-panel",
            "sub": user["oidc_sub"],
            "exp": int(time.time()) + 300,
            "groups": groups,
            **claims,
        }
        token = jwt.encode(
            body, self.signing_key, algorithm="RS256", headers={"kid": self.kid}
        )
        return {
            "Authorization": "Bearer " + token,
            "Origin": "https://panel.example.org",
        }

    def test_delivery_and_cross_team_denial(self):
        response = self.public.post(
            "/api/tasks",
            headers=self.headers(),
            data={
                "brief": "Implement clamp",
                "team": "payments",
                "test_cmd": "python test_clamp.py",
            },
            files=[("files", ("core.py", b"# fixture\n"))],
        )
        self.assertEqual(response.status_code, 200, response.text)
        task = response.json()["task_id"]
        self.assertEqual(
            self.public.get(
                "/api/tasks/" + task, headers=self.headers("silo")
            ).status_code,
            403,
        )
        request = self.requests[-1]
        self.assertEqual(request["team_id"], "payments")
        self.assertEqual(request["user_id"], "ana")
        self.assertEqual(request["models"], ["local-coder", "local-coder-small"])
        team = next(team for team in self.teams if team["id"] == "payments")
        self.assertEqual(
            request["max_budget"], team["litellm"]["task_budget_usd"]["standard"]
        )
        job = self.kubelet.jobs[-1]
        spec = job["spec"]["template"]["spec"]
        session = next(c for c in spec["containers"] if c["name"] == "session")
        env = {e["name"]: e.get("value") for e in session["env"]}
        tester = spec["initContainers"][0]
        self.assertFalse(
            any(
                e["name"] in ("LITELLM_KEY", "TASK_TOKEN", "LITELLM_MINT_KEY")
                for e in tester.get("env", [])
            )
        )
        callback = {"X-Task-Token": env["TASK_TOKEN"]}
        self.assertEqual(
            self.internal.get(
                "/internal/tasks/" + task + "/input", headers=callback
            ).status_code,
            200,
        )
        self.assertEqual(
            self.public.get(
                "/internal/tasks/" + task + "/input", headers=self.headers()
            ).status_code,
            404,
        )
        body = json.dumps(
            {
                "model": "local-coder",
                "messages": [{"role": "user", "content": "Implement clamp"}],
            }
        ).encode()
        request = urllib.request.Request(
            self.llm + "/v1/chat/completions",
            data=body,
            headers={
                "Authorization": "Bearer " + env["LITELLM_KEY"],
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request) as answer:
            code = json.load(answer)["choices"][0]["message"]["content"]
        generated = self.temp / "test_clamp.py"
        generated.write_text(
            code + "\nassert clamp(-1,0,10)==0\nassert clamp(20,0,10)==10\n"
        )
        tested = subprocess.run(
            [sys.executable, str(generated)],
            env={"PATH": os.defpath},
            capture_output=True,
            text=True,
        )
        self.assertEqual(tested.returncode, 0, tested.stderr)
        self.assertEqual(
            self.internal.post(
                "/internal/tasks/" + task + "/status",
                headers=callback,
                json={"stage": "verifying"},
            ).status_code,
            200,
        )
        bundle = archive({"core.py": code, "VERIFY.txt": "stdlib clamp tests passed\n"})
        delivered = self.internal.post(
            "/internal/tasks/" + task + "/bundle",
            headers={
                **callback,
                "X-Bundle-Verified": "true",
                "Content-Type": "application/gzip",
            },
            content=bundle,
        )
        self.assertEqual(delivered.status_code, 200, delivered.text)
        self.assertEqual(
            self.public.get(
                "/api/tasks/" + task + "/bundle", headers=self.headers()
            ).content,
            bundle,
        )
        self.assertEqual(self.model_calls, ["local-coder"])

    def test_directory_and_disabled_module(self):
        self.assertEqual(
            self.public.get("/api/me", headers=self.headers()).json()["teams"],
            ["payments"],
        )
        self.assertEqual(
            self.public.get(
                "/api/me", headers=self.headers(sub="unknown-sub")
            ).status_code,
            403,
        )
        self.assertEqual(
            self.public.get(
                "/api/me", headers=self.headers(iss="https://wrong.example.org")
            ).status_code,
            401,
        )
        with patch.object(self.panel, "SESSION_JOBS_ENABLED", False):
            response = self.public.post(
                "/api/tasks", headers=self.headers(), data={"brief": "disabled"}
            )
            self.assertEqual(response.status_code, 501)
        directory = Path(os.environ["ORG_DIRECTORY"]) / "users.json"
        saved = directory.read_text()
        users = json.loads(saved)
        next(user for user in users if user["slug"] == "ana")["status"] = "suspended"
        try:
            directory.write_text(json.dumps(users))
            self.assertEqual(
                self.public.get("/api/me", headers=self.headers()).status_code, 403
            )
        finally:
            directory.write_text(saved)


if __name__ == "__main__":
    unittest.main(verbosity=2)

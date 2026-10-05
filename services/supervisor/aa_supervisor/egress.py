"""All externally visible bytes pass through this module."""
import http.client
import json
import os
import re
import stat
import sys
import time
import secrets
from pathlib import Path
from urllib.parse import urlsplit

MASK = "[REDACTED]"


def safe_open(path, flags=os.O_RDONLY, mode=0o600, fifo=False):
    if not hasattr(os, "O_NOFOLLOW"):
        raise OSError("O_NOFOLLOW unavailable")
    path = Path(path).absolute()
    directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:-1]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = following
        fd = os.open(path.name, flags | os.O_NOFOLLOW, mode, dir_fd=directory)
    finally:
        os.close(directory)
    kind = os.fstat(fd).st_mode
    if not (stat.S_ISFIFO(kind) if fifo else stat.S_ISREG(kind)):
        os.close(fd)
        raise OSError("unexpected shared file type")
    return fd


class Egress:
    def __init__(self, patterns=(), sink=None, extra_names=()):
        self.sink = sink
        self.count = 0
        names = json.loads(Path(__file__).with_name("secret_key_names.json").read_text())
        names += list(extra_names)
        names += ["access_token", "refresh_token", "accessToken", "refreshToken", "api_key", "apiKey",
                  "authorization", "aws_secret_access_key", "aws_session_token", "secret_access_key"]
        self.secret_names = {n.lower() for n in names}
        self.patterns = [re.compile(p, re.I | re.S) for p in [
            r"-----BEGIN [A-Z0-9 ]+-----.*?(?:-----END [A-Z0-9 ]+-----|$)",
            r"\bBearer\s+[^\s\"\\,}]+",
            r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
            r"\b(?:sk-(?:ant-|proj-)?|gh[pousr]_|github_pat_|xox[baprcs]-|xapp-|glpat-|AKIA|ASIA|ya29\.|1//|rt_|oai-rt-)[A-Za-z0-9_./+-]{12,}",
            r"[?&](?:sig|token|api_key|access_token)=[^&\s\"']+",
            r"(?<=://)[^/@\s:]+:[^/@\s]+@",
            *[rf"(?:{re.escape(n)})[\"']?\s*[:=]\s*[\"']?[^\s\"',}}]+" for n in names],
            *patterns,
        ]]

    def redact(self, value):
        text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
        for pattern in self.patterns:
            text, count = pattern.subn(MASK, text)
            self.count += count
        return text

    def encode(self, value):
        # Redact individual strings before serialisation so escaping cannot hide tokens.
        def clean(v):
            if isinstance(v, str):
                return self.redact(v)
            if isinstance(v, dict):
                return {self.redact(str(k)): MASK if str(k).lower() in self.secret_names else clean(x)
                        for k, x in v.items()}
            if isinstance(v, list):
                return [clean(x) for x in v]
            return v
        return (json.dumps(clean(value), sort_keys=True) + "\n").encode()

    def send(self, value, socket=None, file=None):
        data = self.encode(value)
        if socket is not None:
            socket.sendall(data)
        elif file is not None:
            file.write(data)
            file.flush()
        elif self.sink:
            self.sink(data)
        else:
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()

    def write_fd(self, fd, data):
        data = self.redact(data.decode("utf-8", "strict")).encode()
        offset = 0
        while offset < len(data):
            written = os.write(fd, data[offset:])
            if written <= 0:
                raise OSError("egress write failed")
            offset += written

    def save_json(self, path, value):
        path = Path(path)
        temporary = path.with_name(".state-" + secrets.token_hex(16))
        fd = safe_open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            self.write_fd(fd, self.encode(value))
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def post_json(self, url, payload, timeout, token_path):
        parsed = urlsplit(url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("credential-free endpoint required")
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        ):
            raise ValueError("TLS required")
        body = self.encode(payload)
        if len(body) > 65536:
            raise ValueError("egress size exceeded")
        cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        deadline = time.monotonic() + timeout
        for attempt in range(2):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("delivery deadline exceeded")
            token = Path(token_path).read_text().strip()
            conn = cls(parsed.hostname, parsed.port, timeout=remaining)
            try:
                conn.request("POST", parsed.path, body, {
                    "Authorization": "Bearer " + token, "Content-Type": "application/json"})
                reply = conn.getresponse()
                data = reply.read(65537)
                if reply.status == 401 and attempt == 0:
                    continue
                if reply.status >= 400 or len(data) > 65536:
                    raise OSError("receiver refused request")
                return json.loads(data) if data else {}
            except OSError:
                if attempt:
                    raise
            finally:
                conn.close()
        raise OSError("receiver refused request")

    def request_json(self, method, url, payload=None, timeout=10, token_path=None):
        """Pace's cluster HTTP is explicit; payloads still cross this choke point."""
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            raise ValueError("invalid service endpoint")
        cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        for attempt in range(2):
            headers = {"Content-Type": "application/json"}
            if token_path:
                headers["Authorization"] = "Bearer " + Path(token_path).read_text().strip()
            conn = cls(parsed.hostname, parsed.port, timeout=timeout)
            try:
                conn.request(method, parsed.path, None if payload is None else self.encode(payload), headers)
                reply = conn.getresponse()
                data = reply.read(65537)
                if len(data) > 65536:
                    raise ValueError("service response too large")
                if reply.status == 401 and token_path and attempt == 0:
                    continue
                return reply.status, json.loads(data) if data else {}
            finally:
                conn.close()

    def open_stream(self, url, token_path, acknowledged):
        parsed = urlsplit(url)
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}):
            raise ValueError("TLS required")
        cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        for attempt in range(2):
            conn = cls(parsed.hostname, parsed.port, timeout=2)
            try:
                conn.request("GET", parsed.path, headers={"Authorization": "Bearer " + Path(token_path).read_text().strip(),
                    "Accept": "text/event-stream", "Last-Event-ID": str(acknowledged)})
                response = conn.getresponse()
                if response.status == 401 and attempt == 0:
                    conn.close()
                    continue
                if response.status != 200:
                    raise OSError("command stream refused")
                return conn, response
            except Exception:
                conn.close()
                raise
        raise OSError("command stream refused")

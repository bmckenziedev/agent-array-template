"""Outbound SSE connector; foreground-owned worker has bounded shutdown."""
import json
import queue
import random
import select
import threading
import time
from urllib.parse import urlsplit


class Connector:
    def __init__(self, service, url, token_path="/var/run/agent-array/supervisor-token/token"):
        parsed = urlsplit(url)
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        ):
            raise ValueError("TLS required")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("invalid console endpoint")
        self.service, self.url, self.parsed = service, url.rstrip("/"), parsed
        self.token_path = token_path
        self.frames = queue.Queue(maxsize=256)
        self.stop_event = threading.Event()
        self.acked = 0
        self.thread = None
        self.connection = None
        self.up = False
        self.unacked = {}

    def enqueue(self, frame):
        # Encode before retention: even the retry queue holds only redacted payloads.
        self.frames.put_nowait(json.loads(self.service.egress.encode(frame)))

    def post(self, frame):
        seq = frame.get("seq")
        if isinstance(seq, int):
            if seq <= self.acked:
                return {"ack_seq": self.acked}
            if len(self.unacked) >= 256 and seq not in self.unacked:
                raise OSError("output acknowledgement backlog full")
            frame = json.loads(self.service.egress.encode(frame))
            self.unacked[seq] = frame
        result = self.service.egress.post_json(self.url + "/v1/supervisor/events", frame, 5, self.token_path)
        ack = result.get("ack_seq")
        if isinstance(ack, int) and self.acked <= ack <= self.service.seq:
            self.acked = ack
            self.unacked = {n: f for n, f in self.unacked.items() if n > ack}
        return result

    def hello(self):
        env = self.service.env
        return {"type": "hello", "protocol_version": 1, "pod": env.get("AA_POD_NAME"),
                "user": env.get("AA_USER"), "tool": env.get("AA_TOOL"),
                "account": env.get("AA_ACCOUNT"), "supervisor_version": "0.1.0", "resume_seq": self.acked}

    @staticmethod
    def backoff(attempt):
        return min(60, 2 ** min(attempt, 6)) * random.uniform(0.5, 1.0)

    def run_once(self):
        self.post(self.hello())
        for seq in sorted(self.unacked):
            frame = self.unacked.get(seq)
            if frame:
                self.post(frame)
        conn, response = self.service.egress.open_stream(self.url + "/v1/supervisor/commands", self.token_path, self.acked)
        self.connection = conn
        try:
            self.up = True
            self.service.audit("supervisor.connector_connect")
            started = time.monotonic()
            metrics_at = started
            buffer = b""
            while not self.stop_event.is_set() and time.monotonic() - started < 3000:
                while not self.frames.empty():
                    frame = self.frames.get_nowait()
                    try:
                        self.post(frame)
                    except Exception:
                        self.frames.put_nowait(frame)
                        raise
                if time.monotonic() >= metrics_at:
                    self.post({"type": "metrics", "metrics": self.service.snapshot_metrics()})
                    metrics_at = time.monotonic() + self.service.policy.get("metrics_push_s", 60)
                if not select.select([response.fp], [], [], 0.5)[0]:
                    continue
                chunk = response.read1(65536)
                if not chunk:
                    raise OSError("command stream ended")
                buffer += chunk
                if len(buffer) > 65536:
                    raise OSError("command stream size exceeded")
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if line.startswith(b"data:"):
                        command = json.loads(line[5:].decode("utf-8", "strict"))
                        self.post({"type": "response", "request_id": command.get("request_id"),
                                   "result": self.service.execute(command)})
        finally:
            self.up = False
            conn.close()
            self.connection = None
            self.service.audit("supervisor.connector_disconnect")

    def run(self):
        attempt = 0
        while not self.stop_event.is_set():
            try:
                self.run_once()
                attempt = 0
            except Exception:
                self.service.audit("supervisor.connector_error", "error")
                self.stop_event.wait(self.backoff(attempt))
                attempt += 1

    def start(self):
        self.thread = threading.Thread(target=self.run, name="supervisor-connector")
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.connection:
            self.connection.close()
        if self.thread:
            self.thread.join()

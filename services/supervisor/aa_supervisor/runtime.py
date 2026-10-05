"""Fixed tmux operations; no process namespace inspection or PID signals."""
import json
import os
import re
import subprocess
import time
import secrets
import stat
from pathlib import Path

from .egress import safe_open

TOOLS = {"claude", "codex", "kimi", "aa-rc", "aa-codex", "aa-kimi", "aa-spawn-run"}
SOCKET = "/run/aa-tmux/tmux-1000/default"


class Tmux:
    def run(self, *args):
        return subprocess.run(["tmux", "-S", SOCKET, *args], check=True,
                              capture_output=True, text=True, timeout=10).stdout

    def panes(self):
        output = self.run("list-panes", "-a", "-F",
                          "#{pane_id}\t#{window_id}\t#{pane_current_command}\t#{pane_current_path}\t#{window_name}\t#{pane_start_command}")
        result = []
        for line in output.splitlines():
            parts = line.split("\t")
            if len(parts) == 6 and re.fullmatch(r"%[0-9]+", parts[0]):
                result.append(dict(zip(["pane", "window", "command", "cwd", "name", "start_command"], parts)))
        return result

    def input(self, pane, text):
        current = next((p for p in self.panes() if p["pane"] == pane), None)
        if not current or current["command"] not in TOOLS or listener(current):
            raise ValueError("not_drivable")
        self.run("send-keys", "-t", pane, "-l", "--", text)
        current = next((p for p in self.panes() if p["pane"] == pane), None)
        if not current or current["command"] not in TOOLS or listener(current):
            raise ValueError("not_drivable")
        self.run("send-keys", "-t", pane, "Enter")


def listener(pane):
    # Start metadata can only narrow authority; names/titles never confer it.
    return pane["command"] == "aa-rc" or bool(re.search(
        r"(?:^|[ /])aa-rc(?:\s|$)|\bremote-control\b", pane.get("start_command", "")))


def validate_cwd(cwd):
    if not isinstance(cwd, str) or not re.fullmatch(r"/work(?:/[A-Za-z0-9._/-]+)?", cwd):
        raise ValueError("gate_rejected")
    if os.path.normpath(cwd).replace("\\", "/") != cwd or ".." in cwd.split("/"):
        raise ValueError("gate_rejected")
    return cwd


def output_payload(record, text):
    if record["kind"] == "discovered":
        return "text", text
    events = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(event, dict):
            events.append(event)
    if not events:
        return "text", text
    tool_types = {"tool_use", "tool_result", "tool_call", "function_call", "function_call_output"}
    tool = False
    for event in events:
        if event.get("type") in tool_types:
            tool = True
        nested = event.get("payload", {})
        if isinstance(nested, dict) and nested.get("type") in tool_types:
            tool = True
        message = event.get("message", {})
        if isinstance(message, dict) and isinstance(message.get("content"), list):
            tool = tool or any(isinstance(block, dict) and block.get("type") in tool_types for block in message["content"])
    return "tool" if tool else "text", {"events": events}


class Registry:
    def __init__(self, env, tmux, shared="/run/aa", transcripts="/transcripts", fifo_factory=None, shared_opener=None):
        self.env, self.tmux = env, tmux
        self.spawned = {}
        self.transcripts = Path(transcripts)
        self.shared = Path(shared)
        self.parent = None
        self.fifos = {}
        self.create_fifo = fifo_factory or getattr(os, "mkfifo", None)
        self.open_shared = shared_opener or safe_open

    def launch(self, tool, cwd, brief, egress, work_item=None, estate_id=None, stop_grace=10):
        if self.parent is None:
            parent = self.shared / ("spawn-" + secrets.token_hex(8) + "." + secrets.token_hex(8))
            parent.mkdir(mode=0o700)
            if not stat.S_ISDIR(parent.lstat().st_mode):
                raise OSError("unsafe spawn parent")
            self.parent = parent
        identity = secrets.token_hex(8) + "." + secrets.token_hex(8)
        directory = self.parent / identity
        directory.mkdir(mode=0o700)
        (directory / "out").mkdir(mode=0o700)
        metadata_fd = self.open_shared(directory / "out/runner.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            egress.write_fd(metadata_fd, egress.encode({"stop_grace_s": stop_grace}))
        finally:
            os.close(metadata_fd)
        if self.create_fifo is None:
            raise OSError("FIFO requires POSIX")
        self.create_fifo(directory / "input", 0o600)
        # RDWR prevents FIFO rendezvous from blocking the command loop.
        fd = self.open_shared(directory / "input", os.O_RDWR | getattr(os, "O_NONBLOCK", 0), fifo=True)
        try:
            output = self.tmux.run("new-window", "-d", "-t", "aa", "-n", "aa-" + identity,
                "-c", cwd, "-P", "-F", "#{pane_id}\t#{window_id}", "--", "/usr/local/bin/aa-spawn-run",
                "--dir", str(directory), "--tool", tool)
            location = output.strip().split("\t")
            if len(location) != 2 or not re.fullmatch(r"%[0-9]+", location[0]) or not re.fullmatch(r"@[0-9]+", location[1]):
                raise OSError("invalid launch handle")
            pane, window = location
            data = ({"type": "user", "message": {"role": "user", "content": brief}}
                    if tool == "claude" else brief)
            # The shared input FIFO is an egress destination and is redacted too.
            encoded = egress.encode(data) if tool == "claude" else (egress.redact(brief) + "\n").encode()
            egress.write_fd(fd, encoded)
        except Exception:
            os.close(fd)
            raise
        if tool == "claude":
            self.fifos[identity] = fd
        else:
            os.close(fd)
        record = dict(self.base(tool), id=identity, kind="spawned", cwd=cwd, pane=pane,
            window=window, state="running", drivable="full" if tool == "claude" else "signal-only",
            drive_via="handle" if tool == "claude" else "tmux", directory=str(directory),
            work_item=work_item, estate_id=estate_id, started_at=time.time(), last_activity=time.time())
        self.spawned[identity] = record
        return record

    def input_spawned(self, identity, text, egress):
        fd = self.fifos[identity]
        egress.write_fd(fd, egress.encode({"type": "user", "message": {"role": "user", "content": text}}))

    def close_input(self, identity):
        fd = self.fifos.pop(identity, None)
        if fd is not None:
            os.close(fd)

    def base(self, tool):
        return {"owner": self.env.get("AA_USER"), "team": self.env.get("AA_TEAM"),
                "account": self.env.get("AA_ACCOUNT"), "tool": tool, "session_id": None,
                "work_item": None, "started_at": None, "last_activity": None,
                "permission_mode": self.env.get("AA_PERMISSION_MODE"),
                "permission_routing": "unavailable", "git_identity": {
                    "name": self.env.get("AA_USER"), "email": self.env.get("AA_USER_EMAIL"),
                    "login": self.env.get("AA_USER_GITHUB"), "credential": "unknown",
                    "source": "registry" if self.env.get("AA_USER") else "none"}}

    def discover(self, policy):
        result = dict(self.spawned)
        try:
            panes = self.tmux.panes()
        except (OSError, subprocess.SubprocessError):
            panes = []
        occupied = {r["pane"] for r in result.values()}
        for p in panes:
            if p["command"] not in TOOLS or p["pane"] in occupied:
                continue
            tool = {"aa-rc": "claude", "aa-codex": "codex", "aa-kimi": "kimi"}.get(p["command"], p["command"])
            if tool == "aa-spawn-run":
                continue
            identity = "pane-" + p["pane"][1:]
            result[identity] = dict(self.base(tool), id=identity, kind="discovered", cwd=p["cwd"],
                pane=p["pane"], window=p["window"], state="running", drive_via="tmux",
                drivable="full" if policy.get("drive_discovered") and not listener(p) else "signal-only")
        for tool in ["claude", "codex"]:
            root = self.transcripts / tool
            if not root.exists():
                continue
            for path in sorted(root.rglob("*.jsonl"), key=lambda p: p.lstat().st_mtime, reverse=True):
                if path.is_symlink():
                    continue
                sid = path.stem
                if any(r.get("session_id") == sid for r in result.values()):
                    continue
                age = time.time() - path.stat().st_mtime
                if age > 3600:
                    continue
                try:
                    fd = safe_open(path)
                    try:
                        prefix = os.read(fd, 16384).decode("utf-8", "replace")
                    finally:
                        os.close(fd)
                    cwd = None
                    for line in prefix.splitlines():
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue
                        cwd = event.get("cwd") or event.get("payload", {}).get("cwd") or cwd
                    matching = [r for r in result.values() if r["kind"] == "discovered"
                                and r["tool"] == tool and r["cwd"] == cwd and cwd is not None
                                and not r.get("session_id")]
                    if len(matching) == 1:
                        matching[0]["session_id"] = sid
                        continue
                except OSError:
                    pass
                identity = "transcript-" + sid
                result[identity] = dict(self.base(tool), id=identity, session_id=sid, kind="headless",
                    cwd=None, pane=None, window=None, state="running" if age < 60 else "idle",
                    drivable="none", drive_via="none", transcript=str(path))
        return result

    def output(self, record):
        if record["kind"] == "discovered":
            return self.tmux.run("capture-pane", "-t", record["pane"], "-p", "-J")
        path = record.get("transcript") or str(Path(record["directory"]) / "out/events.jsonl")
        fd = self.open_shared(path)
        try:
            size = os.fstat(fd).st_size
            os.lseek(fd, max(0, size - 65536), os.SEEK_SET)
            return os.read(fd, 65536).decode("utf-8", "replace")
        finally:
            os.close(fd)

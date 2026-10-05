"""Unix-only request channels. Control mount is private to the sidecar."""
import json
import os
import selectors
import socket
import stat
import time


class UnixAPI:
    def __init__(self, supervisor, control="/run/aa-supervisor/control.sock", permission="/run/aa/permission.sock"):
        if any(not p.startswith("/") or ":" in p for p in [control, permission]):
            raise ValueError("TCP addresses refused")
        self.supervisor = supervisor
        self.paths = [control, permission]
        self.selector = selectors.DefaultSelector()
        self.sockets = []
        self.running = True

    def start(self):
        for index, path in enumerate(self.paths):
            if os.path.lexists(path):
                if not stat.S_ISSOCK(os.lstat(path).st_mode):
                    raise ValueError("unsafe socket path")
                os.unlink(path)
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.bind(path)
            os.chmod(path, 0o600)
            sock.listen(16)
            sock.setblocking(False)
            self.selector.register(sock, selectors.EVENT_READ, index)
            self.sockets.append(sock)

    def dispatch(self, request, channel):
        if not isinstance(request, dict):
            return {"error": "gate_rejected", "decision": "deny"}
        if channel == 1:
            if request.get("command") != "permission.request":
                self.supervisor.audit("supervisor.permission_socket_rejected", "deny")
                return {"error": "forbidden", "decision": "deny"}
            return self.supervisor.permission(request)
        if request.get("command") == "permission.request":
            self.supervisor.audit("supervisor.control_socket_rejected", "deny")
            return {"error": "forbidden"}
        return self.supervisor.execute(request, local=True)

    def serve(self):
        self.start()
        clients = {}
        try:
            while self.running:
                self.supervisor.tick()
                for key, _ in self.selector.select(0.2):
                    if isinstance(key.data, int):
                        conn, _ = key.fileobj.accept()
                        conn.setblocking(False)
                        clients[conn] = {"channel": key.data, "buffer": b"", "started": time.monotonic()}
                        self.selector.register(conn, selectors.EVENT_READ, clients[conn])
                    else:
                        conn, state = key.fileobj, key.data
                        chunk = conn.recv(65536)
                        state["buffer"] += chunk
                        if not chunk or len(state["buffer"]) > 65536:
                            if len(state["buffer"]) > 65536:
                                self.supervisor.audit("supervisor.command", "deny", detail={"reason": "frame_size"})
                            self.close_client(conn, clients)
                            continue
                        if b"\n" in state["buffer"]:
                            try:
                                request = json.loads(state["buffer"].split(b"\n", 1)[0].decode("utf-8", "strict"))
                                reply = self.dispatch(request, state["channel"])
                                if state["channel"] == 1 and reply.get("decision") == "pending":
                                    state["pending"] = reply
                                    state["buffer"] = b""
                                    continue
                            except (ValueError, UnicodeError, TypeError):
                                self.supervisor.audit("supervisor.command", "deny", detail={"reason": "frame_format"})
                                reply = {"error": "gate_rejected", "decision": "deny"}
                            self.respond(conn, reply, clients)
                for conn, state in list(clients.items()):
                    item = state.get("pending")
                    if item and (item["decision"] != "pending" or time.monotonic() >= item["deadline"]):
                        if item["decision"] == "pending":
                            item["decision"] = "deny"
                            self.supervisor.audit("permission.decision", "deny", detail={"decision": "deny", "reason": "timeout"})
                        self.respond(conn, {"decision": item["decision"]}, clients)
                    elif not item and time.monotonic() - state["started"] > 10:
                        self.close_client(conn, clients)
        finally:
            for conn in list(clients):
                self.close_client(conn, clients)
            for sock in self.sockets:
                self.selector.unregister(sock)
                sock.close()
            self.selector.close()
            for path in self.paths:
                if os.path.lexists(path) and stat.S_ISSOCK(os.lstat(path).st_mode):
                    os.unlink(path)

    def close_client(self, conn, clients):
        self.selector.unregister(conn)
        conn.close()
        clients.pop(conn, None)

    def respond(self, conn, reply, clients):
        try:
            conn.setblocking(True)
            conn.settimeout(2)
            self.supervisor.egress.send(reply, socket=conn)
        except OSError:
            self.supervisor.audit("supervisor.command", "error", detail={"reason": "peer_disconnected"})
        finally:
            self.close_client(conn, clients)

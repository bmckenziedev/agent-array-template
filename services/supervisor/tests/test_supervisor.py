"""Portable offline policy, redaction, discovery and renderer evidence."""
import ast
import copy
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aa_supervisor.egress import Egress, safe_open
from aa_supervisor.policy import authorize, classify, Limits
from aa_supervisor.runtime import Registry, Tmux, validate_cwd, output_payload
from aa_supervisor.service import Supervisor
from aa_supervisor.transport import UnixAPI

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


plugin = load("supervisor_render", ROOT / "render_plugin.py")
generator = load("supervisor_secret_names", ROOT / "tools/gen_secret_names.py")
verifier = load("console_verifier", ROOT / "console_ref/verify.py")


def config():
    # Tests use the stdlib parser, same subset as production rendering.
    parser = load("yaml_subset", REPO / "tools/render/aa_render/yamlsub.py")
    return parser.parse_yaml_subset((ROOT / "org.component.defaults.yaml").read_text())["defaults"]


def policy():
    result = config()
    result.update(holder={"slug": "ana", "oidc_sub": "subject-ana", "status": "active"},
        principals=[{"slug": "bo", "oidc_sub": "subject-bo", "status": "active"}],
        automation_identities=["observer"], breakglass_group="emergency", leads=["bo"])
    return result


HOLDER = {"kind": "user", "id": "ana", "sub": "subject-ana"}


class FakeTmux:
    def __init__(self):
        self.rows = [{"pane": "%1", "window": "@1", "command": "claude", "cwd": "/work", "name": "renamed"},
                     {"pane": "%2", "window": "@2", "command": "bash", "cwd": "/work", "name": "shell"}]
        self.calls = []

    def panes(self):
        return self.rows

    def run(self, *args):
        self.calls.append(args)
        if args[0] == "capture-pane":
            return "synthetic text"
        if args[0] == "new-window":
            return "%3\t@3\n"
        return ""

    def input(self, pane, text):
        return Tmux.input(self, pane, text)


class Security(unittest.TestCase):
    def test_invariant_holder_only_spawn_full_actor_command_kind_matrix(self):
        p = policy()
        p["leads_can_view"] = True
        p["deciders"]["money"] = ["bo"]
        actors = {
            "holder": HOLDER,
            "local": {"kind": "local"},
            "lead": {"kind": "user", "id": "bo", "sub": "subject-bo"},
            "breakglass": {"kind": "user", "id": "bo", "sub": "subject-bo", "groups": ["emergency"]},
            "automation": {"kind": "automation", "id": "observer"},
            "unknown": {"kind": "user", "id": "unknown", "sub": "unknown"},
        }
        commands = ["sessions.list", "sessions.output", "sessions.spawn", "sessions.input",
                    "sessions.interrupt", "sessions.stop", "sessions.link", "permission.decide"]
        allowed = {
            "holder": set(commands), "local": set(commands),
            "lead": {"sessions.list", "sessions.output", "permission.decide"},
            "breakglass": {"sessions.list", "sessions.output", "sessions.interrupt", "sessions.stop"},
            "automation": {"sessions.list", "sessions.output", "sessions.stop"}, "unknown": set(),
        }
        for kind in ["spawned", "discovered", "headless"]:
            for name, actor in actors.items():
                for cmd in commands:
                    with self.subTest(kind=kind, actor=name, cmd=cmd):
                        error = authorize(p, actor, cmd, "ticket" if name == "breakglass" else None, "money")
                        self.assertEqual(error is None, cmd in allowed[name])
                        if cmd == "sessions.spawn" and name in {"lead", "automation", "breakglass"}:
                            self.assertEqual(error, "seat_interactive_only")

    def test_unknown_suspended_offboarded_denied_before_spawn(self):
        for state in ["suspended", "offboarded"]:
            p = policy()
            p["holder"]["status"] = state
            self.assertEqual(authorize(p, HOLDER, "sessions.spawn"), "forbidden")

    def test_owner_null_only_breakglass_can_stop(self):
        p = policy()
        self.assertEqual(authorize(p, HOLDER, "sessions.stop", owner=False), "forbidden")
        self.assertEqual(authorize(p, {"kind": "automation", "id": "observer"}, "sessions.stop", owner=False), "forbidden")
        actor = {"kind": "user", "id": "bo", "sub": "subject-bo", "groups": ["emergency"]}
        self.assertIsNone(authorize(p, actor, "sessions.stop", "ticket", owner=False))

    def test_active_breakglass_outside_team_is_known_without_lead_authority(self):
        p = policy()
        p["principals"] = []
        p["known_users"] = [{"slug": "bo", "oidc_sub": "subject-bo", "status": "active"}]
        actor = {"kind": "user", "id": "bo", "sub": "subject-bo", "groups": ["emergency"]}
        self.assertIsNone(authorize(p, actor, "sessions.stop", "ticket"))
        self.assertEqual(authorize(p, actor, "sessions.spawn", "ticket"), "seat_interactive_only")
        self.assertEqual(authorize(p, actor, "sessions.input", "ticket"), "forbidden")

    def test_invariant_no_inbound_listeners(self):
        for path in (ROOT / "aa_supervisor").rglob("*.py"):
            source = path.read_text()
            self.assertNotIn("AF_INET", source)
            self.assertNotIn("AF_INET6", source)
        with self.assertRaises(ValueError):
            UnixAPI(None, "127.0.0.1:8081")

    def test_invariant_no_credential_access_no_proc_or_pid_signal(self):
        for path in (ROOT / "aa_supervisor").rglob("*.py"):
            text = path.read_text()
            self.assertNotIn("/proc/", text)
            self.assertNotIn("os.kill(", text)
            self.assertNotIn("SYS_PTRACE", text)
            self.assertNotIn("pipe-pane", text)

    def test_invariant_redaction_single_egress_writes(self):
        for path in (ROOT / "aa_supervisor").rglob("*.py"):
            if path.name == "egress.py":
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = ast.unparse(node.func)
                    self.assertNotIn(name, {"print", "os.write", "sys.stdout.write", "sys.stderr.write"}, str(path))
                    self.assertFalse(name.endswith((".sendall", ".write_text", ".write_bytes")), str(path))

    def test_invariant_session_cannot_use_supervisor_mount_boundary(self):
        for tool in ["claude", "codex", "kimi"]:
            text = (REPO / f"sessions/{tool}/sts-supervised/k8s/statefulset.per-user-tool.tmpl.yaml").read_text()
            self.assertEqual(text.count("mountPath: /run/aa-supervisor"), 1)
            self.assertIn("shareProcessNamespace: false", text)

    def test_invariant_audit_every_command(self):
        captured = []
        service = Supervisor({"AA_USER": "ana"}, Egress(sink=captured.append), FakeTmux())
        with patch.object(service, "reload"):
            service.policy = policy()
            for cmd in ["sessions.list", "unknown", "sessions.spawn"]:
                service.execute({"command": cmd, "actor": {"kind": "user", "id": "unknown"}})
        events = [json.loads(line)["event"] for line in captured]
        self.assertEqual(events, ["sessions.list", "supervisor.command", "sessions.spawn", "session.spawn"])

    def test_dockerfile_from_matches(self):
        def base(path):
            return next(l for l in path.read_text().splitlines() if l.startswith("FROM "))
        self.assertEqual(base(ROOT / "Dockerfile"), base(REPO / "sessions/claude/image/Dockerfile"))


class Redaction(unittest.TestCase):
    def setUp(self):
        self.egress = Egress()

    def test_positive_negative_and_idempotent(self):
        for token in ["sk-ant-" + "a" * 32, "ghp_" + "b" * 32, "Bearer synthetic-token",
                      "eyJabc.defghi.signature", "https://user:pass@example.org/path",
                      "-----BEGIN PRIVATE KEY-----\nsynthetic\n-----END PRIVATE KEY-----"]:
            result = self.egress.redact(token)
            self.assertNotEqual(result, token)
            self.assertEqual(self.egress.redact(result), result)
        for value in ["read /work/report.md", "abc123", "ana@example.org", "sha256:short"]:
            self.assertEqual(self.egress.redact(value), value)

    def test_secret_names_freshness(self):
        self.assertEqual(json.loads((ROOT / "aa_supervisor/secret_key_names.json").read_text()), generator.names(REPO))

    def test_named_secret_json_values_are_masked(self):
        for name in self.egress.secret_names:
            result = json.loads(self.egress.encode({name: "synthetic-sensitive-value"}))
            self.assertNotIn("synthetic-sensitive-value", str(result))

    def test_partial_pem_and_extra_long_runs(self):
        self.assertEqual(self.egress.redact("-----BEGIN PRIVATE KEY-----\npartial"), "[REDACTED]")
        redactor = Egress([r"\b[a-f0-9]{32,}\b"])
        self.assertEqual(redactor.redact("a" * 40), "[REDACTED]")

    def canary(self, source):
        token = "sk-ant-" + "C" * 40
        value = {source: token}
        class Socket:
            def sendall(self, data):
                self.data = data
        for sink in ["connector", "control", "notifier", "hook", "adapter", "audit", "log"]:
            captured = []
            egress = Egress(sink=captured.append)
            egress.send({"sink": sink, "payload": value})
            self.assertNotIn(token.encode(), b"".join(captured))
            conn = Socket()
            egress.send(value, socket=conn)
            self.assertNotIn(token.encode(), conn.data)

    def test_stream_json_canary(self): self.canary("stream-json")
    def test_tmux_pane_canary(self): self.canary("pane")
    def test_transcript_canary(self): self.canary("transcript")
    def test_cwd_canary(self): self.canary("cwd")
    def test_brief_canary(self): self.canary("brief")
    def test_permission_input_canary(self): self.canary("permission")
    def test_error_message_canary(self): self.canary("error")


class GateAndDiscovery(unittest.TestCase):
    def test_stream_tool_events_stay_structured_and_redacted(self):
        secret = "sk-ant-" + "A" * 32
        event = {"type": "assistant", "message": {"content": [{"type": "tool_use", "input": {"token": secret}}]}}
        kind, payload = output_payload({"kind": "spawned"}, json.dumps(event))
        self.assertEqual(kind, "tool")
        self.assertNotIn(secret.encode(), Egress().encode(payload))
    def test_fake_stream_json_spawn_fifo_drive_and_brief_absent_from_argv(self):
        with tempfile.TemporaryDirectory() as tmp:
            def fifo(path, mode):
                Path(path).touch()
            def open_shared(path, flags=os.O_RDONLY, mode=0o600, fifo=False):
                return os.open(path, flags, mode)
            tmux = FakeTmux()
            registry = Registry({"AA_USER": "ana"}, tmux, shared=tmp,
                                fifo_factory=fifo, shared_opener=open_shared)
            egress = Egress(sink=lambda _: None)
            record = registry.launch("claude", "/work", "synthetic brief", egress)
            try:
                self.assertNotIn("synthetic brief", str(tmux.calls))
                self.assertEqual(record["window"], "@3")
                registry.input_spawned(record["id"], "second input", egress)
                messages = [json.loads(line) for line in (Path(record["directory"]) / "input").read_text().splitlines()]
                self.assertEqual([m["message"]["content"] for m in messages], ["synthetic brief", "second input"])
                out = Path(record["directory"]) / "out/events.jsonl"
                out.write_text('{"type":"system","session_id":"cli-id"}\n')
                self.assertIn("cli-id", registry.output(record))
            finally:
                registry.close_input(record["id"])
    def test_input_gate_size_control_utf8_rate(self):
        limits, p = Limits(lambda: 1), policy()
        p.update(input_max_bytes=3, input_max_per_minute=1)
        self.assertEqual(limits.input("a", "abcd", p), "size")
        self.assertEqual(limits.input("a", "\x01", p), "control")
        self.assertEqual(limits.input("a", "\ud800", p), "utf8")
        self.assertIsNone(limits.input("a", "\n\t", p))
        self.assertEqual(limits.input("a", "x", p), "rate")

    def test_classification_highest_risk_unknown_extra(self):
        self.assertEqual(classify("read", {"command": "purchase and chmod"}), "access-change")
        self.assertEqual(classify("unclassified", {}), "unknown")
        self.assertEqual(classify("mcp__billing__charge_card", {}), "money")
        self.assertEqual(classify("custom", "danger", [{"category": "money", "tool_glob": "cust*", "arg_regex": "danger"}]), "money")

    def test_discovery_excludes_shell_and_remote_control_signal_only(self):
        tmux = FakeTmux()
        tmux.rows.append({"pane": "%3", "window": "@3", "command": "aa-rc", "cwd": "/work", "name": "rc"})
        registry = Registry({"AA_USER": "ana", "AA_TEAM": "payments", "AA_ACCOUNT": "seat"}, tmux)
        records = registry.discover(policy())
        self.assertEqual(set(records), {"pane-1", "pane-3"})
        self.assertEqual(records["pane-3"]["drivable"], "signal-only")
        self.assertEqual(records["pane-1"]["owner"], "ana")

    def test_never_type_shell_and_literal_keys_separate_enter(self):
        tmux = FakeTmux()
        with self.assertRaises(ValueError):
            tmux.input("%2", "unsafe")
        self.assertEqual(tmux.calls, [])
        tmux.input("%1", "C-c; shell")
        self.assertEqual(tmux.calls, [("send-keys", "-t", "%1", "-l", "--", "C-c; shell"), ("send-keys", "-t", "%1", "Enter")])

    def test_remote_control_listener_metadata_only_narrows_drive(self):
        tmux = FakeTmux()
        tmux.rows[0]["start_command"] = "/usr/local/bin/aa-rc"
        records = Registry({}, tmux).discover(policy())
        self.assertEqual(records["pane-1"]["drivable"], "signal-only")
        with self.assertRaises(ValueError): tmux.input("%1", "brief")

    def test_transcript_headless_and_window_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "codex").mkdir()
            (root / "codex/session-abc.jsonl").write_text('{}\n')
            registry = Registry({}, FakeTmux(), transcripts=tmp)
            records = registry.discover(policy())
            record = records["transcript-session-abc"]
            self.assertEqual(record["drivable"], "none")
            self.assertEqual(record["session_id"], "session-abc")
            self.assertIsNone(record["owner"])

    def test_cwd_validation(self):
        self.assertEqual(validate_cwd("/work/payments"), "/work/payments")
        for cwd in ["/etc", "/work/../etc", "/work/a;command", "/work//a", "relative"]:
            with self.assertRaises(ValueError): validate_cwd(cwd)

    @unittest.skipUnless(os.name == "posix", "O_NOFOLLOW requires POSIX")
    def test_shared_files_no_follow_regular_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "file").write_text("synthetic")
            (root / "link").symlink_to(root / "file")
            with self.assertRaises(OSError): safe_open(root / "link")
            with self.assertRaises(OSError): safe_open(root)


class Renderer(unittest.TestCase):
    def test_strictest_merge_two_teams(self):
        c = config()
        c["team_policies"] = {
            "a": {"permission_tiers": {"network": "auto"}, "policy_may_allow": ["money"], "deciders": {"money": ["ana", "bo"]}, "human_timeout_s": 40},
            "b": {"permission_tiers": {"network": "human"}, "policy_may_allow": [], "deciders": {"money": ["bo"]}, "human_timeout_s": 20}}
        result = plugin.resolve(c, ["a", "b"])
        self.assertEqual(result["permission_tiers"]["network"], "human")
        self.assertEqual(result["policy_may_allow"], [])
        self.assertEqual(result["deciders"]["money"], ["bo"])
        self.assertEqual(result["human_timeout_s"], 20)

    def model(self):
        fixture = json.loads((ROOT / "tests/fixtures/org.fixture.json").read_text())
        fixture["keys"]["C_MONITORING_LITELLM_METRICS_SECRET_KEY"] = "synthetic-metrics-key"
        fixture["org"].setdefault("components", {})["supervisor"] = config()
        return fixture

    def test_only_active_principals_determinism(self):
        model = self.model()
        one, two = {}, {}
        plugin.render(model, one.__setitem__)
        plugin.render(model, two.__setitem__)
        self.assertEqual(one, two)
        for content in one.values():
            obj = json.loads(content)
            if obj["kind"] == "ConfigMap":
                p = json.loads(obj["data"]["policy.json"])
                self.assertTrue(all(u["status"] == "active" for u in p["principals"]))

    def test_egress_cidr_rejections(self):
        for cidr in ["0.0.0.0/0", "::/0", "100.64.0.0/10"]:
            model = self.model()
            model["org"]["components"]["supervisor"]["console_cidrs"] = [cidr]
            with self.assertRaises(ValueError): plugin.render(model, lambda *args: None)


class ConsoleAuthentication(unittest.TestCase):
    def setUp(self):
        self.status = {"authenticated": True, "audiences": ["aa-supervisor"], "user": {
            "username": "system:serviceaccount:aa-u-ana:session", "extra": {
                "authentication.kubernetes.io/pod-name": ["pod-a"]}}}
        self.namespace = {"metadata": {"labels": {"example.org/kind": "user-sessions", "example.org/user": "ana"}}}
        self.hello = {"user": "ana", "pod": "pod-a"}

    def verify(self):
        return verifier.verify("synthetic", self.hello, lambda *args: {"status": self.status},
            lambda *args: self.namespace, "aa", "aa-u-", "example.org")

    def test_accept_bound_user_pod(self): self.assertEqual(self.verify()["user"], "ana")
    def test_reject_unauthenticated(self):
        self.status["authenticated"] = False
        with self.assertRaises(PermissionError): self.verify()
    def test_reject_hook_and_wrong_audience(self):
        for audience in ["aa-supervisor-hook", "api"]:
            self.status["audiences"] = [audience]
            with self.assertRaises(PermissionError): self.verify()
        for audiences in ["aa-supervisor", ["aa-supervisor", "aa-supervisor-hook"], []]:
            self.status["audiences"] = audiences
            with self.assertRaises(PermissionError): self.verify()
    def test_reject_wrong_sa(self):
        self.status["user"]["username"] = "system:serviceaccount:aa-u-ana:other"
        with self.assertRaises(PermissionError): self.verify()
    def test_reject_wrong_namespace_prefix(self):
        self.status["user"]["username"] = "system:serviceaccount:other:session"
        with self.assertRaises(PermissionError): self.verify()
    def test_reject_wrong_namespace_kind(self):
        self.namespace["metadata"]["labels"]["example.org/kind"] = "system"
        with self.assertRaises(PermissionError): self.verify()
    def test_reject_user_mismatch(self):
        self.hello["user"] = "bo"
        with self.assertRaises(PermissionError): self.verify()
    def test_reject_pod_mismatch_or_missing(self):
        for names in [[], ["other"], ["pod-a", "other"]]:
            self.status["user"]["extra"]["authentication.kubernetes.io/pod-name"] = names
            with self.assertRaises(PermissionError): self.verify()

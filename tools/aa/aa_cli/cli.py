"""User CLI; server RBAC and ingest policy remain the authority."""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

from . import __version__, config, kube, panel, transfer


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="aa")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--from", dest="source", required=True)
    commands.add_parser("login")
    commands.add_parser("whoami")
    sessions = commands.add_parser("sessions").add_subparsers(dest="action", required=True)
    for verb in ("list", "login", "attach", "logs", "scale", "supervise"):
        sub = sessions.add_parser(verb)
        sub.add_argument("--tool", required=verb != "list")
        sub.add_argument("-n", "--namespace")
        if verb in ("login", "attach", "logs", "supervise"):
            sub.add_argument("--pod")
        if verb == "supervise":
            sub.add_argument("supervisor_args", nargs=argparse.REMAINDER)
        if verb == "scale":
            sub.add_argument("replicas", type=int)
            sub.add_argument("--statefulset")
    pace = commands.add_parser("pace").add_subparsers(dest="action", required=True)
    pace.add_parser("status")
    report = pace.add_parser("report")
    report.add_argument("--tool", required=True)
    report.add_argument("--pod")
    report.add_argument("manual")
    for name, verbs in (("work", ("up", "push", "get")), ("snapshot", ("up",))):
        actions = commands.add_parser(name).add_subparsers(dest="action", required=True)
        for verb in verbs:
            sub = actions.add_parser(verb)
            sub.add_argument("--tool", required=True)
            sub.add_argument("--pod")
            sub.add_argument("-n", "--namespace")
            sub.add_argument("--ws", required=True)
            if verb == "get":
                sub.add_argument("--out", required=True)
            else:
                sub.add_argument("--path", required=True)
                sub.add_argument("--estate", required=True)
    send = commands.add_parser("send")
    send.add_argument("--path", required=True)
    send.add_argument("--estate", required=True)
    send.add_argument("--tool", required=True)
    send.add_argument("-m", "--message", required=True)
    get = commands.add_parser("get")
    get.add_argument("task")
    get.add_argument("--out", required=True)
    return root


def output(data) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))


def scale(identity: kube.Identity, tool: str, requested: int, name: str | None) -> int:
    identity.select(tool)
    if requested < 0:
        raise ValueError("replica count cannot be negative")
    account = identity.account(tool)
    if requested > int(account["max_concurrent_sessions"]):
        raise ValueError("replica count exceeds the account max sessions")
    target = min(requested, identity.tier_max())
    items = identity.resources("statefulsets", tool)
    selected = [o for o in items if not name or o["metadata"]["name"] == name]
    if len(selected) != 1:
        raise ValueError("select one labelled StatefulSet with --statefulset")
    others = sum(int(o.get("spec", {}).get("replicas", 0)) for o in items if o not in selected)
    if others + target > int(account["max_concurrent_sessions"]):
        raise ValueError("aggregate replicas exceed the account max sessions")
    obj = selected[0]
    # An optimistic precondition prevents silently overwriting a concurrent scale.
    identity.scoped(["scale", "statefulset", obj["metadata"]["name"],
                     "--replicas", str(target), "--resource-version", obj["metadata"]["resourceVersion"]])
    print(f"replicas: {target}")
    return target


def policy(identity: kube.Identity, estate_id: str) -> dict:
    cm = json.loads(identity.scoped(["get", "configmap", "context-policy", "-o", "json"]))
    estates = json.loads(cm["data"]["estates.json"])
    if isinstance(estates, dict):
        deny = estates.get("deny_globs", [])
        estates = estates["estates"]
        estates = [dict(e, deny_globs=[*deny, *e.get("deny_globs", [])]) for e in estates]
    estate = next((e for e in estates if e["id"] == estate_id), None)
    if estate is None:
        raise ValueError("estate is not in the caller's context-policy")
    owner = estate.get("owner_team")
    if not owner:
        canonical = identity.directory.get("estates.json", [])
        if isinstance(canonical, dict):
            canonical = canonical.get("estates", [])
        record = next((e for e in canonical if e["id"] == estate_id), {})
        owner = record.get("owner_team")
    if not owner and len(identity.user["teams"]) != 1:
        raise ValueError("estate owner policy is ambiguous; transfer refused")
    teams = [t for t in identity.directory["teams.json"] if t["id"] in identity.user["teams"]
             and (not owner or t["id"] == owner)]
    vendors = {}
    for team in teams:
        for classification, allowed in team.get("vendors_allowed", {}).items():
            if classification not in team.get("data_classes_allowed", []):
                continue
            vendors.setdefault(classification, set()).update(allowed)
    return {"estates": estates, "vendors_allowed": {k: sorted(v) for k, v in vendors.items()}}


def upload(identity: kube.Identity, args) -> bytes:
    account = identity.account(args.tool)
    estate = transfer.authorize(policy(identity, args.estate), args.estate,
                                Path(args.path), args.tool, account["vendor"])
    args.origin = estate["_origin"]
    return transfer.archive(Path(args.path), estate)


def sessions(identity: kube.Identity, args) -> None:
    if args.action == "supervise":
        pod = identity.pod(args.tool, args.pod)
        command = args.supervisor_args
        if command[:1] == ["--"]:
            command = command[1:]
        identity.scoped(["exec", "-i", pod, "-c", "supervisor", "--", "aa-supervise",
                         *(command or ["list"])], interactive=True)
        return
    if args.action == "list":
        tools = [args.tool] if args.tool else sorted(identity.user["tools"])
        output([{"tool": tool, "namespace": identity.namespace, "pod": o["metadata"]["name"],
                 "phase": o.get("status", {}).get("phase")} for tool in tools
                for o in identity.resources("pods", tool)])
        return
    if args.action == "scale":
        scale(identity, args.tool, args.replicas, args.statefulset)
        return
    pod = identity.pod(args.tool, args.pod)
    identity.account(args.tool)
    if args.action == "logs":
        sys.stdout.buffer.write(identity.scoped(["logs", pod, "-c", args.tool]))
        return
    if args.action == "login":
        command = {"claude": ["claude", "auth", "login"], "codex": ["codex", "login", "--device-auth"],
                   "kimi": ["kimi", "login"]}.get(args.tool)
        if command is None:
            raise ValueError("vendor login command is unavailable for this tool")
        identity.scoped(["exec", "-it", pod, "-c", args.tool, "--", *command], interactive=True)
    else:
        identity.scoped(["attach", "-it", pod, "-c", args.tool], interactive=True)


def execute(args) -> None:
    if args.command == "init":
        print(config.init(args.source))
        return
    cfg = config.load()
    if args.command == "login":
        kube.login(cfg)
        print("OIDC context ready")
        return
    identity = kube.Identity(cfg, getattr(args, "namespace", None))
    if args.command == "whoami":
        output({"slug": identity.slug, "teams": identity.user["teams"],
                "namespace": identity.namespace, "tools": identity.user["tools"],
                "accounts": [identity.account(t)["id"] for t in identity.user["tools"]]})
    elif args.command == "sessions":
        sessions(identity, args)
    elif args.command == "pace":
        if args.action == "status":
            accounts = json.loads(kube.run(["get", "--raw", cfg["pace_service_path"].rstrip("/") + "/v1/accounts"]))
            teams = set(identity.user["teams"])
            allowed = {a["id"] for a in identity.directory["accounts.json"]
                       if a.get("holder") == identity.slug or (a["type"] != "seat" and
                       (a.get("owner_team") in teams or "*" in a.get("shared_with", [])
                        or teams.intersection(a.get("shared_with", []))))}
            output([a for a in accounts if a["id"] in allowed])
        else:
            account = identity.account(args.tool)
            names = set(account["windows"])
            seen = set()
            for item in args.manual.split(","):
                name, value = item.split("=")
                if name not in names or name in seen or not 0 <= float(value) <= 100:
                    raise ValueError("invalid usage window or percentage")
                seen.add(name)
            pod = identity.pod(args.tool, args.pod)
            identity.scoped(["exec", pod, "-c", args.tool, "--", "aa-usage-report", "--manual", args.manual])
    elif args.command in ("work", "snapshot"):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", args.ws) or args.ws in ("snap", "tasks"):
            raise ValueError("invalid workspace name")
        pod = identity.pod(args.tool, args.pod)
        if args.action == "get":
            data = identity.scoped(["exec", pod, "-c", args.tool, "--", "aa-snapshot",
                                    "work", "get", "--ws", args.ws])
            transfer.extract_export(data, Path(args.out))
        else:
            data = upload(identity, args)
            identity.scoped(["exec", "-i", pod, "-c", args.tool, "--", "aa-snapshot",
                             args.command, args.action, "--estate", args.estate,
                             "--ws", args.ws, "--tool", args.tool,
                             "--repo", args.origin], data=data)
    elif args.command == "send":
        if not cfg["panel_url"]:
            raise ValueError("panel URL is not configured")
        data = upload(identity, args)
        # The portal owns task state and classification checks. The local ledger is only a cache.
        client = panel.Client(cfg)
        transfer.scan(args.message.encode())
        response = client.create_task(data, args.estate, args.message, args.origin)
        config.write_json(config.directory() / "task-cache.json", response)
        output(response)
    elif args.command == "get":
        if not re.fullmatch(r"[A-Za-z0-9_-]+", args.task):
            raise ValueError("invalid task id")
        data = panel.Client(cfg).call("/api/tasks/" + quote(args.task, safe="") + "/bundle")
        transfer.extract_export(data, Path(args.out))


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        execute(args)
        return 0
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as exc:
        print(f"aa: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

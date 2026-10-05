#!/usr/bin/env python3
"""audit_log_probe.py -- check the live API audit log after 30-audit-logging.sh.

Usage: audit_log_probe.py AUDIT_LOG PROBE_NAME SINCE_EPOCH

30-audit-logging.sh makes a few harmless probe requests named PROBE_NAME (create and
delete a ConfigMap and a Role, a GET on the exec subresource of a pod that does not
exist, a list of the Secrets in ns default) and then runs this. It reads the JSON-lines audit log and
checks, for events at or after SINCE_EPOCH:

  configmap create/delete   level Metadata, no request or response body
  role create/delete        level Request, request body present
  pods/exec (missing pod)   level Metadata
  secrets list              level Metadata, no request or response body
  invariant                 NO event about secrets/configmaps/tokenreviews/
                            serviceaccounts/token carries a request or response body

It prints one summary line per probe event (verb, resource, level, body yes/no) and
never prints an object, a request URI or a response.
Exit 0 = all checks pass, 1 = a check failed, 2 = unreadable log.
"""
import datetime
import argparse
import json
import sys

SECRET_LIKE = {("", "secrets"), ("", "configmaps"), ("authentication.k8s.io", "tokenreviews")}


def ts(ev):
    t = ev.get("stageTimestamp") or ev.get("requestReceivedTimestamp") or ""
    try:
        return datetime.datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument('path')
    parser.add_argument('probe')
    parser.add_argument('since', type=float)
    parser.add_argument('--actor', default='system:admin')
    parser.add_argument('--namespace', default='default')
    parser.add_argument('--require-groups', action='store_true')
    args = parser.parse_args(argv)
    path, probe, since = args.path, args.probe, args.since
    seen = {}
    leaks = 0
    total = 0
    attribution_failures = 0
    try:
        f = open(path, encoding="utf-8", errors="replace")
    except OSError as e:
        print("cannot read %s: %s" % (path, e))
        return 2
    with f:
        for line in f:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ts(ev) < since:
                continue
            total += 1
            ref = ev.get("objectRef") or {}
            group, res, sub = ref.get("apiGroup", ""), ref.get("resource", ""), ref.get("subresource", "")
            body = "requestObject" in ev or "responseObject" in ev
            if body and ((group, res) in SECRET_LIKE or (res == "serviceaccounts" and sub == "token")):
                leaks += 1
            key = None
            if ref.get("name") == probe and res in ("configmaps", "roles") and ev.get("verb") in ("create", "delete"):
                key = (res, ev["verb"])
            elif ref.get("name") == probe and res == "pods" and sub == "exec":
                key = ("pods/exec", "any")
            elif (res == "secrets" and ev.get("verb") == "list" and ref.get("namespace") == args.namespace
                  and (ev.get("user") or {}).get("username") == args.actor):
                key = ("secrets", "list")
            if key and ev.get("stage") in ("ResponseComplete", "ResponseStarted", "Panic"):
                actor = ev.get('user') or {}
                if actor.get('username') != args.actor or ref.get('namespace') != args.namespace:
                    attribution_failures += 1
                if args.require_groups and not actor.get('groups'):
                    attribution_failures += 1
                seen.setdefault(key, []).append((ev.get("level"), "requestObject" in ev, "responseObject" in ev))

    want = {
        ("configmaps", "create"): ("Metadata", False),
        ("configmaps", "delete"): ("Metadata", False),
        ("roles", "create"): ("Request", True),
        ("roles", "delete"): ("Request", None),
        ("pods/exec", "any"): ("Metadata", False),
        ("secrets", "list"): ("Metadata", False),
    }
    fails = attribution_failures
    if attribution_failures:
        print('  FAIL probe attribution or groups missing')
    print("audit events since %d: %d" % (since, total))
    for key, (level, need_body) in want.items():
        evs = seen.get(key)
        if not evs:
            print("  FAIL %-10s %-6s no event found" % key)
            fails += 1
            continue
        lvl, req_body, resp_body = evs[-1]
        ok = lvl == level
        if need_body is True:
            ok = ok and req_body
        if need_body is False:
            ok = ok and not req_body and not resp_body
        fails += not ok
        print("  %s %-10s %-6s level=%s request-body=%s response-body=%s (want %s%s)" % (
            "ok  " if ok else "FAIL", key[0], key[1], lvl, "yes" if req_body else "no",
            "yes" if resp_body else "no", level,
            ", with request body" if need_body is True else ", no body" if need_body is False else ""))
    if leaks:
        print("  FAIL invariant: %d event(s) about secret-bearing objects carry a body" % leaks)
        fails += 1
    else:
        print("  ok   invariant: no secret-bearing event carries a body")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

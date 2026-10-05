#!/usr/bin/env python3
"""Build and check the agent-array Localhost seccomp profile (see README.md).

The profile is containerd's own RuntimeDefault, exactly as containerd 2.3.4 generated it
for a drop-ALL-capabilities container for the reference fixture (reference/), with two changes so that it
behaves under gVisor (runsc, oci-seccomp=true) the way RuntimeDefault behaves under runc:

  clone3  SCMP_ACT_ERRNO(ENOSYS) -> SCMP_ACT_TRACE
          runsc turns every ERRNO rule into EPERM (it drops errnoRet), and glibc only falls
          back from clone3 to clone on ENOSYS, so threads fail. gVisor answers a TRACE
          verdict with ENOSYS when no ptrace tracer has asked for seccomp events, so glibc
          falls back to clone(), whose namespace flags RuntimeDefault already filters.
  ptrace  allowed -> denied (default action, EPERM)
          a tracer that sets PTRACE_O_TRACESECCOMP could let a traced clone3 run unfiltered
          (namespace flags included). With ptrace denied no tracer can exist in the pod.
  seccomp allowed -> allowed only while SECCOMP_FILTER_FLAG_NEW_LISTENER (flags & 8) is clear
          On Linux a filter stack takes the highest-precedence verdict, and USER_NOTIF outranks
          TRACE (it does not outrank ERRNO, which is why RuntimeDefault is not affected). A
          process could install its own listener filter for clone3 and let a sibling answer
          CONTINUE: clone3 then runs unfiltered. Measured under runc with TRACE alone (a child
          was created, CLONE_NEWUSER included). gVisor has no listeners (EINVAL), but the profile
          is a file on every node and any pod may name it, so it must be safe under runc too.

Everything else (default action, architectures, every other rule) is copied unchanged.

  seccomp_profile.py build [--clone3 trace|errno|allow] [--keep-ptrace] [--keep-listener] [-o FILE]
      write the profile (defaults = the shipped profile) to FILE or stdout
  seccomp_profile.py check FILE
      prove FILE is at least as strict as the reference: exit 1 and say why if not
  seccomp_profile.py diff FILE
      list every difference from the reference (what is added, removed or changed)
"""
import argparse
import copy
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
REFERENCE = HERE / "reference" / "containerd-2.3.4-runtime-default-dropall.json"
SHIPPED = HERE / "profiles" / "runtime-default-clone3-enosys.json"
# Actions a rule may take in a profile that is at least as strict as RuntimeDefault for
# a syscall RuntimeDefault does not allow: never ALLOW (or LOG, which also runs it).
REFUSING = {"SCMP_ACT_ERRNO", "SCMP_ACT_TRACE", "SCMP_ACT_KILL", "SCMP_ACT_KILL_THREAD",
            "SCMP_ACT_KILL_PROCESS", "SCMP_ACT_TRAP"}


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# SECCOMP_FILTER_FLAG_NEW_LISTENER (uapi/linux/seccomp.h); seccomp(op, flags, uargs) takes flags as arg 1.
NEW_LISTENER = 8
# seccomp(2) with the listener flag clear. SCMP_CMP_MASKED_EQ compares (arg & value) == valueTwo,
# and valueTwo is omitted (0) like in the reference's clone rule.
SECCOMP_NO_LISTENER_RULE = {"action": "SCMP_ACT_ALLOW", "names": ["seccomp"],
                            "args": [{"index": 1, "op": "SCMP_CMP_MASKED_EQ", "value": NEW_LISTENER}]}


def build(ref, clone3="trace", keep_ptrace=False, keep_listener=False):
    """Return the profile dict derived from the reference RuntimeDefault spec."""
    prof = copy.deepcopy(ref)
    rules = []
    seen_clone3 = False
    for rule in prof["syscalls"]:
        names = list(rule["names"])
        if not keep_ptrace and rule["action"] == "SCMP_ACT_ALLOW" and "ptrace" in names:
            names.remove("ptrace")           # falls through to the default action (EPERM)
            if not names:
                continue
            rule = dict(rule, names=names)
        if not keep_listener and rule["action"] == "SCMP_ACT_ALLOW" and "seccomp" in names and not rule.get("args"):
            names.remove("seccomp")          # re-added below, without the listener flag
            if not names:
                continue
            rule = dict(rule, names=names)
        if names == ["clone3"]:
            seen_clone3 = True
            rule = {"names": ["clone3"]}
            if clone3 == "errno":            # what RuntimeDefault asks for (runsc makes it EPERM)
                rule.update(action="SCMP_ACT_ERRNO", errnoRet=38)
            elif clone3 == "trace":          # ENOSYS from gVisor and Linux when untraced
                rule.update(action="SCMP_ACT_TRACE")
            elif clone3 == "allow":          # measured only: lets namespace flags through
                rule.update(action="SCMP_ACT_ALLOW")
            else:
                raise SystemExit("unknown --clone3 %r" % clone3)
        rules.append(rule)
    if not seen_clone3:
        raise SystemExit("reference has no clone3 rule; regenerate reference/ (README)")
    if not keep_listener:
        rules.insert(1, copy.deepcopy(SECCOMP_NO_LISTENER_RULE))   # right after the big allow list
    prof["syscalls"] = rules
    return prof


def allowed(spec):
    """Map syscall -> list of arg filters under which it is ALLOWED ([] = unconditionally)."""
    out = {}
    for rule in spec["syscalls"]:
        if rule["action"] not in ("SCMP_ACT_ALLOW", "SCMP_ACT_LOG"):
            continue
        for name in rule["names"]:
            out.setdefault(name, []).append(json.dumps(rule.get("args") or [], sort_keys=True))
    return out


def refused(spec):
    """Map syscall -> action of a non-allowing rule naming it."""
    return {n: r["action"] for r in spec["syscalls"] if r["action"] not in ("SCMP_ACT_ALLOW", "SCMP_ACT_LOG")
            for n in r["names"]}


def listener_blocked(spec):
    """True if no rule lets seccomp(2) install a filter with SECCOMP_FILTER_FLAG_NEW_LISTENER.

    Every ALLOW/LOG rule naming seccomp must carry an arg-1 condition (arguments of one rule
    are ANDed) of the form (flags & mask) == value with the listener bit in mask and clear in value.
    """
    for rule in spec["syscalls"]:
        if rule["action"] not in ("SCMP_ACT_ALLOW", "SCMP_ACT_LOG") or "seccomp" not in rule["names"]:
            continue
        if not any(a.get("index") == 1 and a.get("op") == "SCMP_CMP_MASKED_EQ"
                   and a.get("value", 0) & NEW_LISTENER and not a.get("valueTwo", 0) & NEW_LISTENER
                   for a in rule.get("args") or []):
            return False
    return True


def check(prof, ref):
    """Return a list of reasons PROF is less strict than REF (empty = at least as strict)."""
    bad = []
    if prof.get("defaultAction") != ref["defaultAction"]:
        bad.append("defaultAction %r != reference %r" % (prof.get("defaultAction"), ref["defaultAction"]))
    if prof.get("defaultErrnoRet") != ref.get("defaultErrnoRet"):
        bad.append("defaultErrnoRet differs from the reference")
    if sorted(prof.get("architectures", [])) != sorted(ref.get("architectures", [])):
        bad.append("architectures differ from the reference")
    for key in ("flags", "listenerPath", "listenerMetadata"):
        if prof.get(key):
            bad.append("%s is set (the reference has none)" % key)
    pa, ra = allowed(prof), allowed(ref)
    for name, conds in sorted(pa.items()):
        if name not in ra:
            bad.append("%s is allowed; the reference does not allow it" % name)
            continue
        if "[]" in ra[name]:
            continue                          # reference allows it unconditionally
        for c in conds:
            if c not in ra[name]:
                bad.append("%s allowed under args %s, which the reference does not allow" % (name, c))
    for name, action in refused(prof).items():
        if action not in REFUSING:
            bad.append("%s has action %s" % (name, action))
    # TRACE refuses (ENOSYS) only while nobody can let the call run unfiltered. Two ways exist:
    # a ptrace tracer that asked for seccomp events, and (Linux only; USER_NOTIF outranks TRACE
    # in a filter stack) a process's own seccomp listener filter answered with CONTINUE.
    # So TRACE counts as refusing only with ptrace denied AND the listener flag denied.
    traced = sorted(n for n, act in refused(prof).items() if act == "SCMP_ACT_TRACE")
    if traced and "ptrace" in pa:
        bad.append("%s use SCMP_ACT_TRACE while ptrace is allowed (a tracer could let them run)"
                   % ", ".join(traced))
    if traced and not listener_blocked(prof):
        bad.append("%s use SCMP_ACT_TRACE while seccomp(2) may install a listener filter "
                   "(USER_NOTIF outranks TRACE; a supervisor answering CONTINUE runs the call)"
                   % ", ".join(traced))
    return bad


def diff(prof, ref):
    pa, ra = allowed(prof), allowed(ref)
    pr, rr = refused(prof), refused(ref)
    lines = []
    for n in sorted(set(ra) - set(pa)):
        lines.append("- %s: allowed in reference, %s here" % (n, pr.get(n, "default action (%s)" % prof["defaultAction"])))
    for n in sorted(set(pa) - set(ra)):
        lines.append("+ %s: allowed here, %s in reference" % (n, rr.get(n, "default action")))
    for n in sorted(set(pa) & set(ra)):
        if sorted(pa[n]) != sorted(ra[n]):
            lines.append("~ %s: arg filters differ" % n)
    for n in sorted(set(pr) | set(rr)):
        if n in pr and n in rr and pr[n] != rr[n]:
            lines.append("~ %s: %s here, %s in reference" % (n, pr[n], rr[n]))
    errno_here = {n: r.get("errnoRet") for r in prof["syscalls"] for n in r["names"] if "errnoRet" in r}
    errno_ref = {n: r.get("errnoRet") for r in ref["syscalls"] for n in r["names"] if "errnoRet" in r}
    for n in sorted(set(errno_here) | set(errno_ref)):
        if errno_here.get(n) != errno_ref.get(n):
            lines.append("~ %s: errnoRet %s here, %s in reference" % (n, errno_here.get(n), errno_ref.get(n)))
    return lines


def dumps(prof):
    return json.dumps(prof, indent=1, sort_keys=True) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--clone3", choices=["trace", "errno", "allow"], default="trace")
    b.add_argument("--keep-ptrace", action="store_true")
    b.add_argument("--keep-listener", action="store_true", help="measured only: leave seccomp(2) unfiltered")
    b.add_argument("-o", "--output")
    for name in ("check", "diff"):
        s = sub.add_parser(name)
        s.add_argument("file", nargs="?", default=str(SHIPPED))
    a = ap.parse_args(argv)
    ref = load(REFERENCE)
    if a.cmd == "build":
        text = dumps(build(ref, a.clone3, a.keep_ptrace, a.keep_listener))
        if a.output:
            pathlib.Path(a.output).write_bytes(text.encode())   # LF endings on every OS
        else:
            sys.stdout.write(text)
        return 0
    prof = load(a.file)
    if a.cmd == "diff":
        print("\n".join(diff(prof, ref)) or "identical to the reference")
        return 0
    bad = check(prof, ref)
    for line in bad:
        print("LESS STRICT: " + line)
    if not bad:
        print("OK: %s is at least as strict as %s" % (a.file, REFERENCE.name))
        for line in diff(prof, ref):
            print("  " + line)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

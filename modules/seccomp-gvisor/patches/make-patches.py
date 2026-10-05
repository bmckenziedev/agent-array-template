#!/usr/bin/env python3
"""Generate explicit optional patches against a session-jobs source directory.

Only patch output inside this module is written; target files are never changed.
Exit 1 means an expected anchor or committed patch changed and needs review.
"""
import argparse
import difflib
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]
JOB = HERE.parent / "tests" / "fixtures" / "session-jobs"
PROFILE = "profiles/seccomp-gvisor/runtime-default-clone3-enosys.json"
RULES = (
    "    - expression: >-\n"
    "        has(variables.podSpec.securityContext)\n"
    "        && has(variables.podSpec.securityContext.seccompProfile)\n"
    "        && variables.podSpec.securityContext.seccompProfile.type == 'Localhost'\n"
    "        && has(variables.podSpec.securityContext.seccompProfile.localhostProfile)\n"
    "        && variables.podSpec.securityContext.seccompProfile.localhostProfile == '" + PROFILE + "'\n"
    '      message: "pod must use the exact module profile"\n'
    "    - expression: >-\n"
    "        variables.containers.all(c, !has(c.securityContext) || !has(c.securityContext.seccompProfile))\n"
    '      message: "container seccomp overrides are forbidden"\n'
)
PATCHES = {
    "session-jobs-task-job.yaml.patch": ("task-job.yaml", [
        ("        seccompProfile: {type: RuntimeDefault}\n", 1,
         "        seccompProfile:\n          type: Localhost\n          localhostProfile: " + PROFILE + "\n")]),
    "session-jobs-admission.yaml.patch": ("admission.yaml", [
        ('      message: "containers must not be privileged"\n', 2,
         '      message: "containers must not be privileged"\n' + RULES)]),
    "session-jobs-install-gvisor.sh.patch": ("install-gvisor.sh", [
        ("# seccomp module preflight\n", 1,
         "# seccomp module preflight\n"
         'test -s "${KUBELET_ROOT:-/var/lib/kubelet}/seccomp/' + PROFILE + '" || {\n'
         '  echo "seccomp profile missing; refuse runtime labeling" >&2\n  exit 1\n}\n')]),
}


def read(path):
    data = path.read_bytes()
    if b"\r" in data:
        raise SystemExit("patch target has CR characters: " + str(path))
    return data.decode("utf-8")


def make(name, edits):
    old = read(JOB / name)
    new = old
    for anchor, count, replacement in edits:
        if new.count(replacement) == count:
            continue
        found = new.count(anchor)
        if found != count:
            raise SystemExit(f"{name}: anchor found {found} time(s), expected {count}; review target integration")
        new = new.replace(anchor, replacement)
    rel = "modules/session-jobs/" + name
    return "".join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                        fromfile="a/" + rel, tofile="b/" + rel, n=3))


def main(argv):
    global JOB
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--target", type=pathlib.Path, help="session-jobs source directory; defaults to offline fixtures")
    args = parser.parse_args(argv)
    if args.target:
        JOB = args.target.resolve()
    bad = 0
    for patch_name, (target, edits) in PATCHES.items():
        text = make(target, edits)
        path = HERE / patch_name
        if args.check:
            have = path.read_text(encoding="utf-8") if path.exists() else ""
            good = have == text
            print(patch_name + (": current" if good else ": differs; regenerate and review"))
            bad += not good
        else:
            path.write_bytes(text.encode("utf-8"))
            print("wrote " + patch_name)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

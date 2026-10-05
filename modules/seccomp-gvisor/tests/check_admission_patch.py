#!/usr/bin/env python3
"""Check optional patch integration offline; live mode only performs server dry-runs.

No policies, bindings or namespaces are installed by this harness.
"""
import argparse
import copy
import importlib.util
import json
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import yaml

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("generator", HERE.parent / "patches" / "make-patches.py")
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)


def patched_copy(tmp):
    job = tmp / "modules" / "session-jobs"
    job.mkdir(parents=True)
    for name, _ in g.PATCHES.values():
        (job / name).write_bytes((g.JOB / name).read_bytes())
    for name in g.PATCHES:
        result = subprocess.run(["git", "apply", str(g.HERE / name)], cwd=tmp, capture_output=True, text=True)
        if result.returncode:
            raise SystemExit(result.stderr)
    return job


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--kubeconfig")
    parser.add_argument("--rendered", type=Path, help="rendered diagnostic Job already matching installed policies")
    parser.add_argument("--controller-user", default="system:serviceaccount:kube-system:job-controller")
    args = parser.parse_args(argv)
    if args.offline:
        with tempfile.TemporaryDirectory(prefix="aa-seccomp-patch-") as tmp:
            target = patched_copy(Path(tmp))
            job = yaml.safe_load((target / "task-job.yaml").read_text())
            policies = [o for o in yaml.safe_load_all((target / "admission.yaml").read_text())
                        if o and o["kind"] == "ValidatingAdmissionPolicy"]
            assert len(policies) == 2
            assert job["spec"]["template"]["spec"]["securityContext"]["seccompProfile"] == {
                "type": "Localhost", "localhostProfile": g.PROFILE}
            for policy in policies:
                expressions = " ".join(
                    item["expression"] for item in policy["spec"]["variables"] + policy["spec"]["validations"]
                )
                for field in ("securityContext", "seccompProfile", "localhostProfile", "privileged",
                              "initContainers", "ephemeralContainers"):
                    for access in re.findall(r"(?:variables\.podSpec|c)(?:\.[a-zA-Z0-9_]+)*\." + field + r"\b", expressions):
                        assert "has(" + access + ")" in expressions, "unguarded optional CEL field: " + access
                rules = policy["spec"]["validations"]
                assert sum(g.PROFILE in x["expression"] for x in rules) == 1
                assert any("!has(c.securityContext.seccompProfile)" in x["expression"] for x in rules)
        print("OFFLINE PASS: patches apply, YAML, exact profile, both policies, no container overrides")
        return 0
    if not args.kubeconfig or not args.rendered:
        parser.error("choose --offline, or supply --kubeconfig and --rendered diagnostic Job")
    job = yaml.safe_load(args.rendered.read_text())
    tpl = copy.deepcopy(job["spec"]["template"])
    pod = {"apiVersion": "v1", "kind": "Pod", "metadata": dict(tpl.get("metadata", {})), "spec": tpl["spec"]}
    pod["metadata"].update(name="seccomp-dry-run", namespace=job["metadata"]["namespace"])
    cases = [("positive job", job, True), ("positive pod", pod, True)]
    for kind, obj in (("job", job), ("pod", pod)):
        for label, value in (("RuntimeDefault", {"type": "RuntimeDefault"}),
                             ("Unconfined", {"type": "Unconfined"}),
                             ("wrong path", {"type": "Localhost", "localhostProfile": "profiles/other.json"})):
            bad = copy.deepcopy(obj)
            ps = bad["spec"]["template"]["spec"] if kind == "job" else bad["spec"]
            ps.setdefault("securityContext", {})["seccompProfile"] = value
            cases.append((kind + " " + label, bad, False))
    failed = 0
    for label, obj, expected in cases:
        command = ["kubectl", "--kubeconfig", args.kubeconfig]
        if obj["kind"] == "Pod":
            command += ["--as", args.controller_user]
        command += ["create", "--dry-run=server", "-f", "-"]
        result = subprocess.run(command, input=json.dumps(obj), capture_output=True, text=True)
        passed = (result.returncode == 0) == expected
        # A negative case must fail for this module's rule rather than unrelated RBAC or PSA.
        if not expected:
            passed = passed and "pod must use the exact module profile" in result.stderr
        print(("PASS " if passed else "FAIL ") + label)
        failed += not passed
        if expected and not passed:
            print(result.stderr, file=sys.stderr)
            return 1
    return int(bool(failed))


if __name__ == "__main__":
    sys.exit(main())

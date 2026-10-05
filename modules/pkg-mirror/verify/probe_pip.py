"""pip + devpi probe, run inside a throwaway pod in session-jobs by verify.sh:

    python3 - < probe_pip.py

Self-asserting: prints PASS/FAIL per check, exits 1 if any failed. Stdlib only (the image's
pip makes the venv). It runs the install exactly as modules/session-jobs/orchestrator.py builds it
(--only-binary=:all:, --index-url <mirror>, --trusted-host <mirror host>) and the way
tester.py exports it to a custom test command (PIP_INDEX_URL + PIP_TRUSTED_HOST only).
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

HOST = f"pypi.{os.environ['MIRROR_NAMESPACE']}.svc.cluster.local"
MIRROR = f"http://{HOST}:3141/root/pypi/+simple/"
fails = 0


def check(label, ok, detail=""):
    global fails
    fails += 0 if ok else 1
    print(("PASS " if ok else "FAIL ") + label + ("" if ok else f"  -> {detail}"))


def run(cmd, env=None, timeout=300):
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, "HOME": "/tmp", **(env or {})})
    return p.returncode, (p.stdout + p.stderr).strip()[-300:]


def status(method, path, body=None, headers=None):
    req = urllib.request.Request(f"http://{HOST}:3141{path}", data=body, method=method,
                                 headers=headers or {"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except OSError as e:
        return f"err {e}"


rc, out = run("python3 -m venv /tmp/v")
check("python3 -m venv works in the pod", rc == 0, out)
rc, out = run("/tmp/v/bin/pip install --disable-pip-version-check --no-input -q --only-binary=:all: "
              f"--index-url {MIRROR} --trusted-host {HOST} pytest==9.1.1 && "
              "/tmp/v/bin/python -m pytest --version")
check("pip install pytest==9.1.1 through the mirror (orchestrator flags)", rc == 0 and "pytest 9.1.1" in out, out)
rc, out = run("/tmp/v/bin/pip install --disable-pip-version-check -q --only-binary=:all: six==1.17.0",
              env={"PIP_INDEX_URL": MIRROR, "PIP_TRUSTED_HOST": HOST})
check("pip install with only PIP_INDEX_URL + PIP_TRUSTED_HOST (how tester.py exports it)", rc == 0, out)
rc, out = run("/tmp/v/bin/pip install --disable-pip-version-check --no-input -q --only-binary=:all: "
              "--index-url https://pypi.org/simple --timeout 5 --retries 0 iniconfig==2.3.0 "
              "--force-reinstall --no-deps", timeout=120)
check("pip install straight from pypi.org FAILS (no internet egress)", rc != 0, out)
want = {"PUT /evil (create user)": 403, "PUT /root/evil (create index)": 403,
        "PATCH /root/pypi (change the uplink)": 403, "DELETE /root/pypi": 403,
        "POST /+login as root, no password": 401, "POST 200 KB body": 413}
got = {
    "PUT /evil (create user)": status("PUT", "/evil", json.dumps({"password": "x" * 12}).encode()),
    "PUT /root/evil (create index)": status("PUT", "/root/evil", b"{}"),
    "PATCH /root/pypi (change the uplink)": status("PATCH", "/root/pypi",
                                                   json.dumps(["mirror_url=https://evil.example/simple/"]).encode()),
    "DELETE /root/pypi": status("DELETE", "/root/pypi"),
    "POST /+login as root, no password": status("POST", "/+login", json.dumps({"user": "root", "password": ""}).encode()),
    "POST 200 KB body": status("POST", "/root/pypi/", b"x" * 200000, {"Content-Type": "application/octet-stream"}),
}
for k in want:
    check(f"devpi refuses: {k} -> {want[k]}", got[k] == want[k], f"got {got[k]}")
sys.exit(1 if fails else 0)

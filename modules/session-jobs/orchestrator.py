#!/usr/bin/env python3
"""Per-task code generation through entitled LiteLLM model groups.

Generated code executes only in the credential-free tester sidecar.
"""
import argparse
import json
import os
import pathlib
import re
import secrets
import shlex
import sys
import time
import urllib.parse
import urllib.request

from collections import Counter

from openai import APIConnectionError, APIError, APITimeoutError, OpenAI

WORKSPACE = pathlib.Path(os.environ.get("WORKSPACE", "/workspace"))
WORK = WORKSPACE / "work"
IPC = WORKSPACE / ".ipc"                                   # requests -> tester (it reads)
TESTBOX = pathlib.Path(os.environ.get("TESTBOX", "/testbox"))  # tester's results (we read)
BRIEF = (WORKSPACE / "brief.txt").read_text() if (WORKSPACE / "brief.txt").exists() else ""


def _task_meta() -> dict:
    """task.json from the panel's input (validated, non-secret metadata: task_id,
    mode, base_commit, repos, has_test_cmd). Optional: an older panel sends none."""
    try:
        meta = json.loads((WORKSPACE / "task.json").read_text())
    except (OSError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


TASK_META = _task_meta()
TEST_CMD = os.environ.get("TEST_CMD", "").strip()        # panel-validated; run as argv
MAX_REPAIR = int(os.environ.get("MAX_REPAIR", "2"))
TEST_TIMEOUT = int(os.environ.get("TEST_TIMEOUT", "600"))
# Wall-clock budget for this script, well under the Job's activeDeadlineSeconds
# so there is always time left to bundle + upload. No repair round starts, and no
# test run is granted more than what is left of it.
TASK_BUDGET_SECONDS = int(os.environ.get("TASK_BUDGET_SECONDS", "3300"))
TESTER_READY_TIMEOUT = int(os.environ.get("TESTER_READY_TIMEOUT", "120"))
# Package installs during tests (see module documentation): lifecycle scripts
# off for npm, wheels only for pip dependencies, pinned pytest, and the registries
# are the in-cluster read-only mirror (modules/pkg-mirror), the only package
# source a session pod can reach. The defaults equal task-job.template.yaml.
NPM_IGNORE_SCRIPTS = os.environ.get("TEST_NPM_IGNORE_SCRIPTS", "1") != "0"
PIP_ONLY_BINARY = os.environ.get("TEST_PIP_ONLY_BINARY", "1") != "0"
NPM_REGISTRY = os.environ.get("NPM_REGISTRY", "http://npm.agent-array-pkg-mirror.svc:4873/")
PIP_INDEX_URL = os.environ.get("PIP_INDEX_URL",
                               "http://pypi.agent-array-pkg-mirror.svc:3141/root/pypi/+simple/")
PYTEST_SPEC = os.environ.get("PYTEST_SPEC", "pytest==9.1.1")
START = time.monotonic()

# Only groups included in this task's scoped LiteLLM key may be requested.
TASK_MODELS = [m for m in os.environ.get("TASK_MODELS", "local-coder").split(",") if m]
if not TASK_MODELS:
    raise ValueError("TASK_MODELS must contain at least one entitled model")
M_LOCAL = os.environ.get("MODEL_LOCAL", TASK_MODELS[0])
M_REVIEW = os.environ.get("MODEL_REVIEW", TASK_MODELS[-1])
if M_LOCAL not in TASK_MODELS or M_REVIEW not in TASK_MODELS:
    raise ValueError("selected model is outside TASK_MODELS")
LLM_CALL_TIMEOUT = int(os.environ.get("LLM_CALL_TIMEOUT", "600"))
client = OpenAI(base_url=os.environ["LITELLM_BASE"] + "/v1",
                api_key=os.environ["LITELLM_KEY"], max_retries=0)
VERIFY_LOG: list[str] = []
CALLS_OK = 0
ROUTING: list[str] = []

# Tests run on a copy in the tester's volume, so WORK no longer collects test
# debris; these stay pruned from prompts (and diff_bundler.sh's rsync excludes)
# as a backstop for anything an upload already contained.
DEBRIS_DIRS = {".venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
               ".ruff_cache", ".tox", "htmlcov"}


def remaining() -> float:
    return TASK_BUDGET_SECONDS - (time.monotonic() - START)


def _is_debris(name: str) -> bool:
    return name in DEBRIS_DIRS or name.endswith(".egg-info")


def log(msg: str):
    print(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "component": "session-jobs", "event": "stage.progress",
        "actor": {"user": TASK_META.get("user"), "sa": None, "sub": None},
        "team": TASK_META.get("team"), "target": {"task_id": os.environ.get("TASK_ID")},
        "outcome": "allow", "detail": {}}), flush=True)
    VERIFY_LOG.append(msg)


def report(stage: str):
    """Best-effort stage telemetry to the panel (framing/spec/coding/verifying/
    repairing). Never fatal: the bundle is the product, the stream is cosmetic.
    Same per-task token + endpoint as entrypoint.sh's status()."""
    base, tid, tok = (os.environ.get(k, "") for k in ("PANEL_BASE", "TASK_ID", "TASK_TOKEN"))
    if not (base and tid and tok):
        return
    req = urllib.request.Request(
        f"{base}/internal/tasks/{tid}/status",
        data=json.dumps({"stage": stage}).encode(),
        headers={"Content-Type": "application/json", "X-Task-Token": tok},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=5).close()
    except Exception as e:  # noqa: BLE001 -- telemetry must never break a task
        log("status callback unavailable")


def _short(e: Exception) -> str:
    # Upstream error bodies can echo credentials or prompt text.
    code = getattr(e, "status_code", None)
    return f"{type(e).__name__}: HTTP {code}" if code else type(e).__name__


def join_stream(stream, deadline: float, max_chars: int = 2 * 1024 * 1024) -> str:
    """Bound memory and total streaming time; always close the upstream response."""
    parts, size, finished = [], 0, False
    try:
        for chunk in stream:
            if time.monotonic() >= deadline:
                raise ValueError("model stream exceeded wall-clock budget")
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            content = choice.delta.content or ""
            size += len(content)
            if size > max_chars:
                raise ValueError("model stream exceeded response size limit")
            parts.append(content)
            if choice.finish_reason:
                if choice.finish_reason != "stop":
                    raise ValueError("model stream did not complete normally")
                finished = True
        if not finished or not size:
            raise ValueError("model stream ended without a complete answer")
        return "".join(parts)
    finally:
        stream.close()


def ask(model: str, system: str, user: str, json_mode: bool = False) -> str:
    global CALLS_OK
    if model not in TASK_MODELS:
        raise ValueError("model is outside the task entitlement")
    timeout = min(LLM_CALL_TIMEOUT, remaining() - 300)
    if timeout <= 0:
        raise ValueError("task model-call budget exhausted")
    kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}
    raw = client.chat.completions.with_raw_response.create(
        model=model, messages=[{"role": "system", "content": system},
                               {"role": "user", "content": user}],
        timeout=timeout, **kwargs)
    served = raw.headers.get("x-litellm-model-group") or model
    ROUTING.append(f"{model}->{served}")
    answer = raw.parse().choices[0].message.content or ""
    if not answer:
        raise ValueError("empty model response")
    CALLS_OK += 1
    return answer


def parse_json(raw: str) -> dict:
    """Best-effort JSON from a model reply that may be fenced or prose-wrapped."""
    if not raw:
        raise ValueError("empty model reply")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # strip a ```json ... ``` (or bare ```) fence
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass
    # fall back to the widest {...} span
    start, end = raw.find("{"), raw.rfind("}")
    if 0 <= start < end:
        return json.loads(raw[start:end + 1])
    raise ValueError("no JSON object found in model reply")


def task_context() -> str:
    """The client's repo / base-commit metadata as a prompt preamble ('' if none),
    so a multi-repo task needs no footer in the brief."""
    lines = []
    repos = TASK_META.get("repos")
    if isinstance(repos, list) and repos:
        lines.append("repos: " + ", ".join(str(r)[:200] for r in repos[:32]))
    bc = TASK_META.get("base_commit")
    if isinstance(bc, str) and bc:
        lines.append(f"base commit: {bc[:64]}")
    return ("TASK CONTEXT:\n" + "\n".join(lines) + "\n\n") if lines else ""


def file_manifest() -> str:
    # os.walk + pruning: after the first test run WORK holds a whole venv, and
    # its caches / *.egg-info would otherwise be pasted into every prompt.
    out = []
    for dirpath, dirnames, filenames in os.walk(WORK):
        dirnames[:] = sorted(d for d in dirnames if not _is_debris(d))
        for name in sorted(filenames):
            if name.endswith(".pyc") or name == ".coverage":
                continue
            p = pathlib.Path(dirpath) / name
            rel = p.relative_to(WORK)
            try:
                body = p.read_text()
            except (UnicodeDecodeError, OSError):
                body = "<binary or unreadable>"
            out.append(f"### {rel}\n```\n{body}\n```")
    return "\n\n".join(out) if out else "(no files uploaded)"


def _in_work(rel: str) -> pathlib.Path | None:
    """Resolve a model-supplied path; None unless it is strictly inside WORK and
    outside any .git directory. (A plain startswith() check let '../work2/x'
    through, and '' or '.' named WORK itself. A written .git/config or hook would
    run code in the throwaway repo diff_bundler.sh builds in THIS container.)"""
    root = WORK.resolve()
    dst = (WORK / rel).resolve()
    if dst == root or not dst.is_relative_to(root):
        return None
    if any(part.lower() == ".git" for part in dst.relative_to(root).parts):
        return None
    return dst


def apply_edits(raw: str) -> str:
    """Model returns JSON {files:[{path,content}], delete:[path,...], notes}.

    The whole reply is validated BEFORE anything is written, so a malformed
    reply raises ValueError and leaves WORK untouched (never half-applied).
    """
    data = parse_json(raw)
    if not isinstance(data, dict):
        raise ValueError("coder reply is not a JSON object")
    files, deletes = data.get("files") or [], data.get("delete") or []
    if not isinstance(files, list) or not isinstance(deletes, list):
        raise ValueError("'files' and 'delete' must be lists")
    for f in files:
        if not (isinstance(f, dict) and isinstance(f.get("path"), str)
                and isinstance(f.get("content"), str)):
            raise ValueError(f"each 'files' entry needs string path + content, got {str(f)[:200]}")
    if not all(isinstance(d, str) for d in deletes):
        raise ValueError("'delete' must be a list of path strings")

    for f in files:
        # keep writes inside WORK (reject path traversal from the model)
        dst = _in_work(f["path"])
        if dst is None or dst.is_dir():
            log(f"REFUSED write outside WORK or onto a directory: {f['path']}")
            continue
        content = f["content"]
        # Models only ever see LF (read_text() translates CRLF), so they answer
        # in LF. Re-apply the original file's CRLF so a Windows upload gets a
        # minimal diff instead of every line rewritten.
        if dst.is_file() and b"\r\n" in dst.read_bytes() and "\r\n" not in content:
            content = content.replace("\n", "\r\n")
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(content.encode())  # bytes: no newline translation either way
        log(f"wrote {f['path']} ({len(content)} bytes)")
    for d in deletes:
        tgt = _in_work(d)
        if tgt is not None and tgt.is_file():
            tgt.unlink()
            log(f"deleted {d}")
        elif tgt is not None and tgt.exists():
            log(f"REFUSED delete of non-file: {d}")  # e.g. a directory: unlink() would crash
    return str(data.get("notes", ""))


def _has_pytest_files() -> bool:
    """A plain module + test_*.py upload has no pyproject/pytest.ini; without
    this the tests were skipped and the task still reported verified=true."""
    for dirpath, dirnames, filenames in os.walk(WORK):
        dirnames[:] = [d for d in dirnames if not _is_debris(d)]
        if any(n == "conftest.py" or (n.endswith(".py") and
               (n.startswith("test_") or n.endswith("_test.py"))) for n in filenames):
            return True
    return False


def _pip_index_flags() -> str:
    """--index-url for the configured index, plus --trusted-host when it is plain
    http (the in-cluster mirror): pip ignores an untrusted http index."""
    if not PIP_INDEX_URL:
        return ""
    flags = f" --index-url {shlex.quote(PIP_INDEX_URL)}"
    u = urllib.parse.urlsplit(PIP_INDEX_URL)
    if u.scheme == "http" and u.hostname:
        flags += f" --trusted-host {shlex.quote(u.hostname)}"
    return flags


def _install_steps() -> tuple[list[str], str]:
    """(shell steps that install deps for the detected project type, kind).
    Every piece is a constant or shlex-quoted config -- nothing model- or
    upload-controlled is spliced in. Runs in the tester, on a copy of WORK."""
    if (WORK / "package.json").exists():
        lock = any((WORK / n).exists() for n in ("package-lock.json", "npm-shrinkwrap.json"))
        flags = "--no-audit --no-fund" + (" --ignore-scripts" if NPM_IGNORE_SCRIPTS else "")
        if NPM_REGISTRY:
            # the CLI flag wins over any .npmrc the upload or a model wrote
            flags += f" --registry={shlex.quote(NPM_REGISTRY)}"
        return [f"npm {'ci' if lock else 'install'} {flags}"], "npm"
    if (any((WORK / n).exists()
            for n in ("pyproject.toml", "setup.py", "setup.cfg", "pytest.ini", "tox.ini"))
            or _has_pytest_files()):
        # A venv in the tester's scratch volume (the image rootfs is read-only).
        pip = (".venv/bin/pip install --disable-pip-version-check --no-input -q"
               + (" --only-binary=:all:" if PIP_ONLY_BINARY else "")
               + _pip_index_flags())
        steps = ["python3 -m venv .venv", f"{pip} {shlex.quote(PYTEST_SPEC)}"]
        if any((WORK / n).exists() for n in ("pyproject.toml", "setup.py", "setup.cfg")):
            steps.append(f"{{ {pip} -e . || echo '[tester] pip install -e . failed; "
                         "testing the source tree as-is'; }")
        return steps, "python"
    return [], ""


def test_command() -> str | None:
    steps, kind = _install_steps()
    if kind == "npm":
        try:
            scripts = json.loads((WORK / "package.json").read_text()).get("scripts", {})
            if not isinstance(scripts, dict) or not scripts.get("test"):
                return None
            if any("--passWithNoTests" in str(script) for script in scripts.values()):
                return None
        except (OSError, ValueError, AttributeError):
            return None
    if TEST_CMD and shlex.split(TEST_CMD)[0] in ("npm", "npx") and kind != "npm":
        return None
    if TEST_CMD:
        if "--passWithNoTests" in TEST_CMD:
            return None
        # Panel-validated (single line, no shell metacharacters); still quoted
        # token by token so the shell can only ever see it as a plain argv.
        argv = shlex.split(TEST_CMD)
        path = 'export PATH="$PWD/.venv/bin:$PWD/node_modules/.bin:$PATH"'
        return " && ".join(steps + [path, shlex.join(argv)])
    if kind == "npm":
        return " && ".join(steps + ["npm test"])
    if kind == "python":
        # `python -m pytest` (not .venv/bin/pytest) puts the tree on sys.path, so a
        # bare upload (pkg/ + tests/test_x.py, nothing pip-installable) still imports.
        return " && ".join(steps + [".venv/bin/python -m pytest -q"])
    return None


_test_seq = 0


def _tester_ready() -> bool:
    end = time.monotonic() + TESTER_READY_TIMEOUT
    while time.monotonic() < end:
        if (TESTBOX / ".tester-ready").exists():
            return True
        time.sleep(0.5)
    return False


def tests_executed(output: str) -> bool:
    """Require a supported runner's positive test count, not just exit zero.

    This is execution evidence, not a trusted oracle: uploaded tests still require
    independent review. Unknown/custom runner output stays needs-review.
    """
    output = re.sub(r"\x1b\[[0-9;]*m", "", output)
    if re.search(r"no tests|\b0 (?:tests?|passed)|tests?\s*[:=]\s*0\b|# (?:tests|pass) 0\b", output, re.I):
        return False
    return bool(re.search(r"\b[1-9]\d* passed\b|Tests:\s+[1-9]\d* passed\b|# tests [1-9]\d*\b", output))


def run_tests() -> tuple[bool, str]:
    """Ask the tester sidecar to run the test command on a copy of WORK."""
    global _test_seq
    cmd = test_command()
    if cmd is None:
        return False, "no test command detected -- unverified"
    timeout = int(min(TEST_TIMEOUT, remaining() - 120))
    if timeout < 30:
        return False, f"$ {cmd}\n(not run: the task's time budget is used up)"
    if not _tester_ready():
        return False, f"$ {cmd}\n(not run: the tester sidecar never became ready)"
    _test_seq += 1
    n, nonce = _test_seq, secrets.token_hex(16)
    IPC.mkdir(exist_ok=True)
    tmp = IPC / f".req-{n}.tmp"
    tmp.write_text(json.dumps({"id": n, "nonce": nonce, "cmd": cmd, "timeout": timeout}))
    os.replace(tmp, IPC / f"req-{n}.json")
    res_path = TESTBOX / "results" / f"res-{n}.json"
    end = time.monotonic() + timeout + 120
    res = None
    while time.monotonic() < end:
        try:
            cand = json.loads(res_path.read_text())
        except (OSError, ValueError):
            cand = None
        # The nonce is only in our request: a result planted by an earlier run
        # (all of whose processes the tester killed) cannot match it.
        if isinstance(cand, dict) and cand.get("id") == n and cand.get("nonce") == nonce:
            res = cand
            break
        time.sleep(0.5)
    if res is None:
        return False, f"$ {cmd}\n(the tester gave no result within {timeout + 120}s)"
    out = str(res.get("output", ""))[-6000:]
    if res.get("timed_out"):
        return False, f"$ {cmd}\n(TIMED OUT after {timeout}s; every test process was killed)\n{out}"
    rc = res.get("exit")
    ok = type(rc) is int and rc == 0 and tests_executed(str(res.get("output", "")))
    return ok, f"$ {cmd}\n(exit {rc})\n{out}"


# ---- stages -----------------------------------------------------------------
def stage_intent() -> dict:
    files = file_manifest()
    model = M_REVIEW
    try:
        out = ask(model,
                  "You frame a coding task. Return STRICT JSON with keys: "
                  "intent (1 paragraph), complexity ('trivial'|'full'), "
                  "acceptance_criteria (string[]). No prose outside the JSON.",
                  f"{task_context()}BRIEF:\n{BRIEF}\n\nFILES:\n{files}",
                  json_mode=True)
    except (APIError, ValueError) as e:
        # The whole chain refused (e.g. key or review model budget spent, router down):
        # frame nothing, code from the brief. main() still bundles the result.
        log(f"WARN: intent framing unavailable ({_short(e)}); using the brief as the intent")
        out = ""
    try:
        intent = parse_json(out)
        if not isinstance(intent, dict):  # e.g. a bare JSON list
            raise ValueError("intent reply is not a JSON object")
    except ValueError:
        log("WARN: could not parse intent JSON; defaulting to complexity=full")
        intent = {"intent": BRIEF, "complexity": "full", "acceptance_criteria": []}
    if intent.get("complexity") not in ("trivial", "full"):
        intent["complexity"] = "full"
    log(f"intent.complexity = {intent.get('complexity')}")
    return intent


def stage_spec(intent: dict) -> str:
    try:
        return ask(M_REVIEW,
                   "Turn the framed intent into a concrete implementation spec: files "
                   "to add/change, approach, edge cases, and the tests that prove it. "
                   "Be specific and minimal.",
                   f"{task_context()}INTENT:\n{json.dumps(intent, indent=2)}\n\n"
                   f"FILES:\n{file_manifest()}")
    except (APIError, ValueError) as e:
        # review model over budget / down: code from the brief (the fast-path shape)
        # instead of exiting without a bundle.
        log(f"WARN: spec stage unavailable ({_short(e)}); coding from the brief")
        return ""


def stage_write(spec: str, feedback: str = "") -> str:
    """Ask the coder for edits and apply them. Returns "" on success, else a
    rejection message for the next repair round. A small local model answering
    prose instead of JSON (or the router failing) used to raise straight out of
    main() -> exit 1 -> error-orchestrator with NO bundle; now it costs one
    repair round and, at worst, ends as needs-review WITH a bundle."""
    sys_p = ("You are a precise local coding model. Implement the spec by editing "
             "files. Return STRICT JSON: {\"files\":[{\"path\":\"...\",\"content\":\"...\"}],"
             "\"delete\":[\"...\"],\"notes\":\"...\"}. Full file contents, not diffs. "
             "No prose outside the JSON.")
    user = f"SPEC:\n{spec}\n\nCURRENT FILES:\n{file_manifest()}"
    if feedback:
        user += f"\n\nFIX THESE REVIEW/TEST FAILURES:\n{feedback}"
    try:
        apply_edits(ask(M_LOCAL, sys_p, user, json_mode=True))
        return ""
    except (ValueError, APIError) as e:  # json.JSONDecodeError is a ValueError
        log(f"WARN: coder reply rejected, no files changed this round: {e}")
        return (f"CODER REPLY REJECTED (no files were changed): {e}\n"
                "Reply with ONLY the JSON object described, nothing else.")


def _verdict(v) -> bool:
    """Fail closed. bool("false") is True, and models do emit "pass": "false"."""
    if isinstance(v, bool):
        return v
    return isinstance(v, str) and v.strip().lower() in ("true", "pass", "passed", "yes")


def review(model: str, label: str, criterion: str) -> tuple[bool, str, bool]:
    """-> (pass, issues, reviewer_answered)."""
    try:
        out = ask(model,
                  f"You review the code against the {label}. Return STRICT JSON "
                  "{\"pass\": bool, \"issues\": string[]}. No prose outside the JSON.",
                  f"{criterion}\n\nFILES:\n{file_manifest()}", json_mode=True)
    except (APIError, ValueError) as e:
        # Reviewer down / over budget: an unreviewed result is a FAIL, not a
        # crash -- the code already written still ships as needs-review.
        log(f"{label} review: FAIL (reviewer {model} unavailable: {_short(e)})")
        return False, f"{label} reviewer unavailable: {_short(e)}", False
    try:
        v = parse_json(out)
        if not isinstance(v, dict):
            raise ValueError("verdict is not a JSON object")
    except ValueError:
        log(f"WARN: {label} review returned unparsable JSON; treating as FAIL")
        return False, f"{label} reviewer did not return parseable JSON:\n{out[:1000]}", True
    ok = _verdict(v.get("pass"))
    issues = v.get("issues") or []
    issues = [str(i) for i in issues] if isinstance(issues, list) else [str(issues)]
    log(f"{label} review: {'PASS' if ok else 'FAIL'} {issues}")
    return ok, "\n".join(issues), True


def verify(intent: dict, spec: str, attempt: int = 0,
           last_intent_ok: bool | None = None) -> tuple[bool, str, bool | None, bool]:
    """-> (everything passed, feedback for the coder, intent verdict (None = the
    reviewer never answered), fixable: a rewrite could change the outcome)."""
    tests_ok, tlog = run_tests()
    log("test execution completed")
    fb = [] if tests_ok else [f"TESTS FAILED:\n{tlog}"]
    fixable = not tests_ok
    spec_ok = True
    if spec:
        spec_ok, spec_iss, answered = review(M_REVIEW, "spec", f"SPEC:\n{spec}")
        if not spec_ok:
            fb.append("SPEC REVIEW:\n" + spec_iss)
            fixable = fixable or answered
    reviewer = M_REVIEW
    intent_ok, intent_iss, answered = review(reviewer, "intent",
                                             "INTENT + ACCEPTANCE:\n" + json.dumps(intent))
    if not intent_ok:
        fb.append("INTENT REVIEW:\n" + intent_iss)
        fixable = fixable or answered
    return ((tests_ok and spec_ok and intent_ok), "\n\n".join(fb),
            intent_ok if answered else None, fixable)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run an isolated task pipeline")
    parser.parse_args(argv)
    WORK.mkdir(parents=True, exist_ok=True)
    if TASK_META:
        log("task metadata: " + json.dumps(TASK_META, sort_keys=True))
    report("framing")
    intent = stage_intent()
    # FAST PATH: trivial tasks skip the review model spec entirely.
    if intent.get("complexity") == "trivial":
        spec = ""
    else:
        report("spec")
        spec = stage_spec(intent)
    report("coding")
    rejected = stage_write(spec or BRIEF)
    if CALLS_OK == 0:
        # Framing, spec AND coder all failed: the router itself is down (a spent
        # paid budget still leaves the $0 local tier). Nothing was produced, so
        # fail the task (error-orchestrator) instead of shipping an empty bundle.
        log("FATAL: the model router answered no call (framing, spec and coder all failed)")
        return 1

    report("verifying")
    ok, feedback, intent_ok, fixable = verify(intent, spec)
    attempt = 0
    while not ok and attempt < MAX_REPAIR:
        if not fixable:
            # Tests pass and every failing review is one whose reviewer never
            # answered (budget spent / rate limit / outage): a rewrite cannot fix
            # that, and feeding "reviewer unavailable" to the coder only invites
            # churn on code that passes its tests.
            log("no repair round: the tests pass and the only failures are reviewers that "
                "did not answer (budget / rate limit / outage); shipping for human review")
            break
        if remaining() < TEST_TIMEOUT + 600:
            log(f"no repair round {attempt + 1}: only {int(remaining())}s of the task budget left")
            break
        attempt += 1
        log(f"repair loop {attempt}/{MAX_REPAIR}")
        report("repairing")
        # Tell the coder why its previous reply was thrown away, if it was.
        rejected = stage_write(spec or BRIEF,
                               "\n\n".join(x for x in (rejected, feedback) if x))
        report("verifying")
        ok, feedback, intent_ok, fixable = verify(intent, spec, attempt, intent_ok)

    # Who actually answered each stage, so the bundle says who reviewed it.
    log("routing: " + ", ".join(ROUTING))
    # Log the verdict BEFORE writing VERIFY.txt: it used to be written first, so
    # the bundle's VERIFY.txt never said whether the run passed overall.
    if not ok:
        log("pipeline finished WITHOUT passing verification -- bundle still emitted, see VERIFY.txt")
    else:
        log("pipeline passed")
    (WORKSPACE / "VERIFY.txt").write_text("\n".join(VERIFY_LOG))
    if not ok:
        # Exit 3 so the panel marks the task 'needs-review'. entrypoint.sh still
        # builds + uploads the bundle; exit 3 is NOT treated as a crash.
        return 3

    return 0


if __name__ == "__main__":
    sys.exit(main())

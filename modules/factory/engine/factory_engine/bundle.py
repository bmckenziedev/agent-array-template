"""Bundle assembler: accepted units -> the hand-apply bundle the aa client already applies.

Layout (pipeline/job/diff_bundler.sh + docs/FACTORY-DESIGN.md S9/I6):

  changes.patch        `git diff --cached --binary` against the pristine snapshot. aa convention:
                       single-repo task -> repo-relative paths (aa applies with -p1);
                       multi-repo task  -> `<repo>/...` prefixed paths (aa splits by prefix, -p2).
  patches/<repo>.patch always `<repo>/`-prefixed; from inside the repo: git apply -p2 --3way --index
  files/...            full copies of every changed/added file (same paths as changes.patch)
  RUNBOOK.md           verdict, units table by writer, per-repo results, commit order, pin warning,
                       not-verified list, bounced units, review sample, how to apply
  VERIFY.txt           chronological gate log
  UNITS.json           one row per unit (accepted, bounced, cancelled)
  MANIFEST.json        task, snapshot, base commits, per-repo patch hashes, tool versions, lanes
  evidence/            per-unit gate results, per-repo integration tsc results

Before writing anything, every accepted unit is re-applied onto the pristine base (doc maps are
re-located by symbol, so units on one file compose), then an integration gate runs per repo:
tsc --checkJs on the assembled tree, new-error delta 0 against the baseline. A unit that only
fails in combination is dropped (bounce class INTEGRATION), greedily and boundedly.
Nothing is pushed, committed in a real repo, or synced.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import math
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

from . import __version__, config
from .fsutil import hidden, rmtree
from .gates import GateRunner
from .store import Store



class BundleError(RuntimeError):
    pass


# --------------------------------------------------------------------------- git (hardened)
class _Git:
    """A throwaway repo. Global/system config ignored, no hooks, no fsmonitor, no autocrlf."""

    def __init__(self, root: Path):
        self.root = root
        self.cfg = root.parent / f".{root.name}-gitconfig"
        self.cfg.write_text("", encoding="utf-8")
        self.hooks = root.parent / f".{root.name}-hooks"
        self.hooks.mkdir(exist_ok=True)
        self.env = {k: v for k, v in os.environ.items() if k.upper() in ("PATH", "SYSTEMROOT", "TEMP", "TMP")}
        self.env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(self.cfg), "HOME": str(root.parent),
                         "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})

    def __call__(self, *args: str, binary: bool = False) -> str | bytes:
        r = subprocess.run(["git", "-c", f"core.hooksPath={self.hooks}", "-c", "core.fsmonitor=false",
                            "-c", "core.autocrlf=false", "-c", "core.safecrlf=false", "-c", "commit.gpgsign=false",
                            "-c", "core.quotepath=true", *args], cwd=self.root, env=self.env, capture_output=True,
                           **hidden())
        if r.returncode != 0:
            raise BundleError(f"git {' '.join(args[:2])} failed: {r.stderr.decode('utf-8', 'replace')[-800:]}")
        return r.stdout if binary else r.stdout.decode("utf-8", "replace")


# --------------------------------------------------------------------------- assembly
async def _assemble_file(runner: GateRunner, repo: str, rel: str, units: list[dict], source: str | None = None) -> dict:
    req = {"kind": "assemble_docs", "file": rel,
           "units": [{"unit_id": u["unit_id"], "symbols": u["symbols"], "map": json.loads(u["output"])} for u in units]}
    if source is not None:
        req["source"] = source
    return await runner.run(repo, req, 300)


async def _tsc(runner: GateRunner, repo: str, profile: dict, overlay: dict) -> dict:
    return await runner.run(repo, {"kind": "tsc_check", "profile": profile, "overlay": overlay}, 600)


async def assemble(store: Store, runner: GateRunner, profiles: dict, log=lambda m: None) -> dict:
    """-> {repo: {"files": {rel: text}, "new_files": {rel: text}, "integration": {...}, "dropped": [...]}}"""
    out: dict = {}
    accepted = store.units("accepted")
    by_repo: dict[str, list[dict]] = {}
    for u in accepted:
        by_repo.setdefault(u["repo"], []).append(u)
    for repo, units in sorted(by_repo.items()):
        profile = profiles.get(repo, {})
        files: dict[str, str] = {}
        new_files: dict[str, str] = {}
        dropped: list[dict] = []
        docs = [u for u in units if u["kind"] == "doc_map"]
        for u in units:
            if u["kind"] == "test_gen":
                tf = u["card"]["provides"]["test_file"]
                if tf in new_files:     # submit refuses this; never let a second unit overwrite the first
                    dropped.append({"unit_id": u["unit_id"], "class": "ASSEMBLE",
                                    "message": f"{tf} is already written by another accepted unit"})
                    continue
                new_files[tf] = u["output"]
        by_file: dict[str, list[dict]] = {}
        for u in docs:
            by_file.setdefault(u["file"], []).append(u)
        for rel, fu in sorted(by_file.items()):
            res = await _assemble_file(runner, repo, rel, fu)
            if res.get("stage") == "gate_env" or "spliced" not in res:
                raise BundleError(f"{repo}/{rel}: assembly failed: {res.get('message')}")
            for f in res.get("failed", []):
                dropped.append({"unit_id": f["unit_id"], "class": "ASSEMBLE", "message": f["message"]})
            if res.get("applied"):
                files[rel] = res["spliced"]
        integ: dict = {"checked": False}
        if files:
            full = await _tsc(runner, repo, profile, files)
            if full.get("stage") == "gate_env":
                integ = {"checked": False, "error": full.get("message")}
            elif full.get("ok"):
                integ = {"checked": True, "ok": True, "new": 0, "baseline": full.get("baseline"),
                         "after": full.get("after"), "fixed": full.get("fixed"), "rounds": 1}
            else:
                log(f"{repo}: integration tsc found {len(full.get('new', []))} new error(s); isolating units")
                files, more, rounds = await _isolate(runner, repo, profile, by_file, dropped)
                dropped += more
                final = await _tsc(runner, repo, profile, files) if files else {"ok": True, "new": [], "baseline": full.get("baseline"), "after": full.get("baseline"), "fixed": 0}
                integ = {"checked": True, "ok": bool(final.get("ok")), "new": len(final.get("new", [])),
                         "baseline": final.get("baseline"), "after": final.get("after"), "fixed": final.get("fixed"),
                         "rounds": rounds + 2, "first_errors": full.get("new", [])[:20]}
        out[repo] = {"files": files, "new_files": new_files, "integration": integ, "dropped": dropped}
        for d in dropped:
            u = store.unit(d["unit_id"])
            dossier = {"class": d["class"], "unit_id": d["unit_id"], "repo": repo, "file": u["file"],
                       "symbols": u["symbols"], "kind": u["kind"], "message": d["message"],
                       "best_candidate": {"stage": "pass", "output": u["output"]},
                       "hints": ["passes alone but not together with the other accepted units of this repo"]}
            store.set_unit(d["unit_id"], status="bounced", bounce_class=d["class"], bounce=dossier)
            store.event("unit_bounced", d["unit_id"], cls=d["class"])
    return out


async def _isolate(runner, repo, profile, by_file, dropped_already):
    """Greedy, bounded: add files (then units within a failing file) one at a time and keep only what
    keeps the cumulative tsc delta at 0. At most #files + #units tsc runs; normally never reached."""
    skip = {d["unit_id"] for d in dropped_already}
    kept: dict[str, str] = {}
    dropped: list[dict] = []
    rounds = 0
    for rel, fu in sorted(by_file.items()):
        fu = [u for u in fu if u["unit_id"] not in skip]
        if not fu:
            continue
        whole = await _assemble_file(runner, repo, rel, fu)
        rounds += 1
        r = await _tsc(runner, repo, profile, {**kept, rel: whole["spliced"]})
        if r.get("ok"):
            kept[rel] = whole["spliced"]
            continue
        keep_units: list[dict] = []
        text = None
        for u in fu:
            trial = await _assemble_file(runner, repo, rel, [*keep_units, u])
            rounds += 1
            rr = await _tsc(runner, repo, profile, {**kept, rel: trial["spliced"]})
            if rr.get("ok"):
                keep_units.append(u)
                text = trial["spliced"]
            else:
                errs = "; ".join(f"{e['file']}({e['line']}) {e['code']}: {e['message']}" for e in rr.get("new", [])[:4])
                dropped.append({"unit_id": u["unit_id"], "class": "INTEGRATION",
                                "message": f"new tsc errors only together with other units: {errs}"})
        if text is not None:
            kept[rel] = text
    return kept, dropped, rounds


# --------------------------------------------------------------------------- writing
def _line_ending_warnings(crlf: dict[str, list[str]]) -> list[str]:
    """`aa snapshot up` sends git HEAD blobs (LF). A snapshot copied from a Windows worktree with
    core.autocrlf=true has CRLF bytes the user's index does not hold: the patch is built against those
    bytes, so `git apply --3way` / `aa get --apply` fails ('repository lacks the necessary blob')."""
    out = []
    for repo, files in sorted(crlf.items()):
        out.append(f"{repo}: {len(files)} changed file(s) have CRLF line endings in the snapshot (e.g. {files[0]}). "
                   "If your repo stores LF in git (core.autocrlf=true on Windows), this bundle will not apply: take the "
                   "snapshot with `aa snapshot up` (git HEAD blobs) or `git -c core.autocrlf=false archive`, then re-run. "
                   "Ignore this if the repo really commits CRLF.")
    return out


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _write_text_raw(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _commit_order(repos: list[str]) -> list[str]:
    return sorted(repos)


def _sample(units: list[dict], every: int = 5) -> list[str]:
    ids = sorted((u["unit_id"] for u in units), key=lambda i: hashlib.sha256(i.encode()).hexdigest())
    return sorted(ids[: math.ceil(len(ids) / every)]) if ids else []


def _md_cell(s) -> str:
    return str(s if s is not None else "").replace("|", "\\|").replace("\n", " ")


def build(store: Store, task_dir: Path, assembled: dict, *, out_dir: Path | None = None) -> dict:
    """Write bundle/ + bundle.tgz from the assembled files. Returns the summary."""
    t = store.task()
    cfg = t["config"]
    task_id = t["task_id"]
    repos_cfg = cfg["repos"]
    changed = {r: a for r, a in assembled.items() if a["files"] or a["new_files"]}
    multi = len(changed) > 1
    bdir = (out_dir or task_dir) / "bundle"
    if bdir.exists():
        rmtree(bdir)
    bdir.mkdir(parents=True)
    work = Path(tempfile.mkdtemp(prefix="factory-bundle-", dir=str(task_dir)))
    try:
        gdir = work / "tree"
        gdir.mkdir()
        git = _Git(gdir)
        git("init", "-q")
        git("config", "user.email", "factory@agent-array.local")
        git("config", "user.name", "agent-array factory")
        # 1) baseline = pristine bytes of every file we change
        crlf: dict[str, list[str]] = {}
        for repo, a in changed.items():
            root = Path(repos_cfg[repo]["root"])
            for rel in a["files"]:
                dst = gdir / repo / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / rel, dst)
                if b"\r\n" in dst.read_bytes():
                    crlf.setdefault(repo, []).append(rel)
        warnings = _line_ending_warnings(crlf)
        git("add", "-A")
        git("commit", "-q", "--allow-empty", "-m", "baseline")
        # 2) overlay the assembled files
        for repo, a in changed.items():
            for rel, text in {**a["files"], **a["new_files"]}.items():
                _write_text_raw(gdir / repo / rel, text)
        git("add", "-A")
        name_status = git("diff", "--cached", "--name-status")
        patches = {}
        (bdir / "patches").mkdir()
        for repo in sorted(changed):
            p = git("diff", "--cached", "--binary", "--", f"{repo}/", binary=True)
            (bdir / "patches" / f"{repo}.patch").write_bytes(p)
            patches[repo] = {"path": f"patches/{repo}.patch", "sha256": _sha256(p), "bytes": len(p)}
        if multi or not changed:
            whole = git("diff", "--cached", "--binary", binary=True)
            listing = name_status
        else:
            only = next(iter(changed))
            whole = git("diff", "--cached", "--binary", f"--relative={only}/", binary=True)
            listing = git("diff", "--cached", "--name-status", f"--relative={only}/")
        (bdir / "changes.patch").write_bytes(whole)
        # 3) files/ (same paths as changes.patch)
        for repo, a in changed.items():
            for rel in {**a["files"], **a["new_files"]}:
                src = gdir / repo / rel
                dst = bdir / "files" / (f"{repo}/{rel}" if multi else rel)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
    finally:
        rmtree(work)

    units = store.units()
    attempts = store.attempts()
    by_unit: dict[str, list[dict]] = {}
    for a in attempts:
        by_unit.setdefault(a["unit_id"], []).append(a)
    integ_ok = all(a["integration"].get("ok", False) for a in changed.values() if a["files"]) and \
        all(a["integration"].get("checked") for a in changed.values() if a["files"])
    accepted = [u for u in units if u["status"] == "accepted"]
    bounced = [u for u in units if u["status"] == "bounced"]
    cancelled = [u for u in units if u["status"] == "cancelled"]
    integration_drops = [u for u in bounced if u.get("bounce_class") in ("INTEGRATION", "ASSEMBLE")]
    verified = bool(accepted) and integ_ok and not integration_drops
    verdict = "VERIFIED" if verified else "NEEDS REVIEW"
    reasons = []
    if not accepted:
        reasons.append("no unit was accepted")
    if not integ_ok:
        reasons.append("the integration tsc check did not pass or did not run for every changed repo")
    if integration_drops:
        reasons.append(f"{len(integration_drops)} unit(s) dropped at integration")

    rows = []
    for u in units:
        att = by_unit.get(u["unit_id"], [])
        rows.append({
            "unit_id": u["unit_id"], "repo": u["repo"], "file": u["file"], "kind": u["kind"],
            "difficulty": u["difficulty"], "symbols": u["symbols"], "status": u["status"],
            "writer": u.get("writer"), "lane": u.get("lane"), "model": u.get("model"),
            "generations": u.get("generations") or 0, "gpu_s": u.get("gpu_s") or 0,
            "gates": {a["gen"]: a.get("stage") for a in att},
            "tsc": (u.get("gate") or {}).get("tsc"),
            "bounce_class": u.get("bounce_class"), "template_id": u.get("template_id"),
            "in_bundle": u["status"] == "accepted",
        })
    (bdir / "UNITS.json").write_text(json.dumps({"units": 1, "task_id": task_id, "rows": rows}, indent=1), encoding="utf-8")

    ev = bdir / "evidence"
    for u in units:
        d = ev / "units" / u["unit_id"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "gates.json").write_text(json.dumps({
            "unit_id": u["unit_id"], "status": u["status"], "gate": u.get("gate"), "bounce": u.get("bounce"),
            "attempts": [{k: a[k] for k in ("gen", "lane", "model", "writer", "temperature", "seed", "prompt_tokens",
                                             "completion_tokens", "latency_s", "stop", "envelope_ok", "stage", "ok",
                                             "message", "gate_ms")} for a in by_unit.get(u["unit_id"], [])],
        }, indent=1), encoding="utf-8")
    for repo, a in changed.items():
        (ev / repo).mkdir(parents=True, exist_ok=True)
        (ev / repo / "tsc-integration.json").write_text(json.dumps(a["integration"], indent=1), encoding="utf-8")

    manifest = {
        "bundle": 1, "engine": f"factory-engine {__version__}", "task_id": task_id,
        "snapshot_id": t.get("snapshot_id"), "created": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "layout": "prefixed" if multi else "single", "verdict": verdict, "verified": verified,
        "repos": [{"name": r, "base_commit": repos_cfg[r].get("base_commit"), "tree": repos_cfg[r].get("tree"),
                   "changed_files": sorted({**changed[r]["files"], **changed[r]["new_files"]}),
                   "patch": patches.get(r), "integration": changed[r]["integration"]} for r in sorted(changed)],
        "counts": store.counts(), "tools": cfg.get("tools"), "lanes": config.public_lanes(cfg["lanes"]),
        "gate_runner": cfg.get("gate", {}).get("runner"), "templates": cfg.get("templates", []),
        "warnings": warnings,
    }
    (bdir / "MANIFEST.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")

    (bdir / "VERIFY.txt").write_text(_verify_txt(store, units, by_unit, changed, verdict), encoding="utf-8")
    (bdir / "RUNBOOK.md").write_text(_runbook(t, cfg, units, accepted, bounced, cancelled, by_unit, changed,
                                              listing, multi, verdict, reasons, patches, warnings), encoding="utf-8")

    tgz = (out_dir or task_dir) / "bundle.tgz"
    with tarfile.open(tgz, "w:gz") as tf:
        for p in sorted(bdir.rglob("*")):
            tf.add(p, arcname="./" + p.relative_to(bdir).as_posix(), recursive=False)
    return {"task_id": task_id, "bundle_dir": str(bdir), "bundle_tgz": str(tgz), "verdict": verdict,
            "verified": verified, "reasons": reasons, "warnings": warnings, "layout": manifest["layout"],
            "repos": {r: {"files": len(changed[r]["files"]) + len(changed[r]["new_files"]), "patch": patches[r]["path"]}
                      for r in sorted(changed)},
            "accepted": len(accepted), "bounced": len(bounced), "cancelled": len(cancelled)}


def _verify_txt(store, units, by_unit, changed, verdict) -> str:
    lines = [f"factory verification log - task {store.task()['task_id']}", ""]
    for u in units:
        for a in by_unit.get(u["unit_id"], []):
            env = "envelope ok" if a["envelope_ok"] else f"envelope FAILED ({a['message']})"
            if a["envelope_ok"]:
                g = "gate passed" if a["ok"] else f"gate {a['stage']} failed: {(a['message'] or '').splitlines()[0][:160] if a['message'] else ''}"
            else:
                g = "not gated"
            lines.append(f"{u['unit_id']} gen {a['gen']} {a['lane']} ({a['model']}, t={a['temperature']}): {env}; {g}")
        lines.append(f"{u['unit_id']}: {u['status'].upper()}" + (f" [{u['bounce_class']}]" if u.get("bounce_class") else ""))
    lines.append("")
    for repo, a in sorted(changed.items()):
        i = a["integration"]
        if not i.get("checked"):
            lines.append(f"{repo}: integration tsc NOT RUN {i.get('error') or ''}")
        else:
            lines.append(f"{repo}: integration tsc --checkJs {'passed' if i.get('ok') else 'FAILED'}: "
                         f"{i.get('new')} new error(s), baseline {i.get('baseline')}, after {i.get('after')}")
    c = store.counts()
    lines.append(f"pipeline finished: {c['accepted']} accepted, {c['bounced']} bounced, {c['cancelled']} cancelled, "
                 f"{c['rejected']} rejected at submit; verdict {verdict}")
    return "\n".join(lines) + "\n"


def _runbook(t, cfg, units, accepted, bounced, cancelled, by_unit, changed, listing, multi, verdict, reasons, patches,
             warnings=()) -> str:
    task_id = t["task_id"]
    kinds = sorted({u["kind"] for u in units})
    tpls = ", ".join(x.get("template_id", "?") for x in cfg.get("templates", [])) or "hand-written cards"
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    md = [f"# agent-array factory bundle - task {task_id}", "",
          f"Generated {now} by factory-engine {__version__}. Nothing was pushed or synced; apply by hand.", "",
          "## Brief",
          f"> factory sweep: {', '.join(kinds) or '-'} from {tpls} (snapshot {t.get('snapshot_id') or 'local copy'})",
          f"> {len(accepted)} accepted, {len(bounced)} bounced, {len(cancelled)} cancelled", "",
          "## Verdict",
          f"**{verdict}**" + (f": {'; '.join(reasons)}" if reasons else
                               ": every unit in this bundle passed its gates and the per-repo integration check."), ""]
    if warnings:
        md += ["## Warnings", *[f"- {w}" for w in warnings], ""]
    md += ["## Files changed", "```", listing.strip() or "<none>", "```", ""]
    md += ["## Apply",
           f"With the aa client (if `factory finish --register-aa` was used): `aa get {task_id} --apply`",
           "(new branch aa/<task> per repo, staged, never committed).", "",
           "By hand, from inside each repo:", "```"]
    for repo in sorted(changed):
        md.append(f"git apply -p2 --3way --index {patches[repo]['path']}   # {repo}")
    md += ["```", "If a hunk is rejected, copy the file from `files/` instead.", ""]
    if multi:
        md += ["## Commit order", " -> ".join(_commit_order(sorted(changed))), ""]
    pins = [f"{r}: {', '.join(sorted(cfg['repos'][r].get('pins') or {}))}" for r in sorted(changed) if cfg["repos"][r].get("pins")]
    if pins and "test_gen" in kinds:
        md += ["## Pin warning",
               "Tests were verified against the node_modules shipped with the snapshot. Dependents pin "
               "@example-org/* tags; CI resolves those tags until you tag and bump the pins.", *[f"- {p}" for p in pins], ""]
    md += ["## Units", "| unit | repo | file | symbols | writer | gens | result |", "|---|---|---|---|---|---|---|"]
    for u in units:
        res = u["status"] + (f" ({u['bounce_class']})" if u.get("bounce_class") else "")
        md.append(f"| {_md_cell(u['unit_id'])} | {_md_cell(u['repo'])} | {_md_cell(u['file'])} | "
                  f"{_md_cell(', '.join(u['symbols'] or []))} | {_md_cell(u.get('writer') or '-')} | "
                  f"{u.get('generations') or 0} | {_md_cell(res)} |")
    writers: dict[str, int] = {}
    for u in accepted:
        writers[u.get("writer") or "?"] = writers.get(u.get("writer") or "?", 0) + 1
    md += ["", "Accepted units by writer: " + (", ".join(f"{w} {n}" for w, n in sorted(writers.items())) or "none"), ""]
    md += ["## Per-repo results", "| repo | base commit | files | integration tsc (new / baseline -> after) |", "|---|---|---|---|"]
    for repo in sorted(changed):
        i = changed[repo]["integration"]
        cell = ("not run" if not i.get("checked") else
                f"{'passed' if i.get('ok') else 'FAILED'} ({i.get('new')} / {i.get('baseline')} -> {i.get('after')})")
        base = (cfg["repos"][repo].get("base_commit") or "unknown")[:12]
        md.append(f"| {repo} | {base} | {len(changed[repo]['files']) + len(changed[repo]['new_files'])} | {cell} |")
    md += [""]
    md += ["## What was not verified"]
    if "doc_map" in kinds:
        md += ["- doc_map: the prose of each description is not checked. Types are checked by tsc --checkJs "
               "(new-error delta 0, inside the profile's tsc include scope only), and @param/@returns coverage by the gate.",
               "- Code tokens are proven unchanged (comment-only edits); tests were not run for doc-only changes."]
    if "test_gen" in kinds:
        md += ["- test_gen: these are CHARACTERIZATION tests. They lock in current behaviour, including bugs; the "
               "mutant gate proves they can detect a change, not that the behaviour is correct.",
               "- Generated test files were each run alone, not together with the repo's full suite."]
    sample = _sample(accepted)
    if sample:
        md += ["", "## Frontier review sample (1 in 5)", ", ".join(sample)]
    if bounced:
        md += ["", "## Bounced units (not in this bundle)", "| unit | class | first failure |", "|---|---|---|"]
        for u in bounced:
            b = u.get("bounce") or {}
            first = (b.get("message") or b.get("first_failure") or "")
            md.append(f"| {_md_cell(u['unit_id'])} | {_md_cell(u.get('bounce_class'))} | {_md_cell(first[:160])} |")
        md += ["", f"Dossiers: `factory bounces {task_id}` (best candidate, attempts, hints)."]
    if cancelled:
        md += ["", f"## Cancelled units", ", ".join(u["unit_id"] for u in cancelled)]
    md += ["", "## Verification recorded by the pipeline",
           "See `VERIFY.txt` (chronological gate log), `UNITS.json`, and `evidence/`.", ""]
    return "\n".join(md)


# --------------------------------------------------------------------------- aa client hand-off
def register_aa(summary: dict, store: Store, repo_roots: dict[str, Path]) -> dict:
    """Write an aa ledger record and place bundle.tgz where `aa get <task> --apply` looks for it
    (<repo>/.aa/bundles/<task>/bundle.tgz), using aa's own modules. Offline: no panel involved."""
    aa_dir = config.ENGINE_DIR.parent.parent / "tools" / "aa"
    if str(aa_dir) not in sys.path:
        sys.path.insert(0, str(aa_dir))
    from aa_cli import gitutil, ledger  # type: ignore  # noqa: E402

    t = store.task()
    cfg = t["config"]
    task_id = t["task_id"]
    repos = sorted(summary["repos"])
    if not repos:
        raise BundleError("the bundle changes no repo; nothing to register")
    missing = [r for r in repos if r not in repo_roots]
    if missing:
        raise BundleError(f"--repo-root missing for: {', '.join(missing)} (NAME=PATH of your checkout)")
    rows = []
    for r in repos:
        root = gitutil.toplevel(Path(repo_roots[r]).resolve())
        base = cfg["repos"][r].get("base_commit") or "HEAD"
        if not gitutil.resolve_commit(root, base):
            raise BundleError(f"{root}: base commit {base[:12]} of the snapshot is not in this checkout")
        rows.append({"root": str(root), "name": r, "prefix": r if len(repos) > 1 else "", "base": base,
                     "branch": None, "origin": None, "pathspecs": ["."], "worktree": False, "dirty": []})
    tgz = Path(summary["bundle_tgz"])
    primary = Path(rows[0]["root"])
    gitutil.ensure_info_exclude(primary)
    dest = primary / ".aa" / "bundles" / task_id
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(tgz, dest / "bundle.tgz")
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    rec = {"task_id": task_id, "created": now, "panel_url": None,
           "brief": f"factory sweep ({', '.join(x.get('template_id', '?') for x in cfg.get('templates', [])) or 'cards'})",
           "files": sum(v["files"] for v in summary["repos"].values()), "bytes": tgz.stat().st_size,
           "excluded": {}, "repos": rows, "test_cmd": None, "mode": "factory",
           "last_stage": "done" if summary["verified"] else "needs-review", "stage_at": now,
           "bundle_dir": str(dest)}
    ledger.save(rec)
    return {"ledger": str(ledger.tasks_dir() / f"{task_id}.json"), "bundle": str(dest / "bundle.tgz"),
            "apply": f"aa get {task_id} --apply"}

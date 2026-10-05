"""Command entry point and output-directory lifecycle."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

from .key_names import GLOBAL_GROUPS, KEY_NAMES
from .lint import key_sets, lint
from .model import OrgError, load_model
from .plugins import render_plugins, safe_path
from .secrets_index import render_secrets
from .templates import RenderError, render_templates
from .yamlsub import YamlError

MARKER = ".rendered-by-aa-render"


def render(root, model, exclude=()):
    outputs = {}

    def emit(relpath, text, source, header=True):
        relpath = safe_path(relpath).as_posix()
        if relpath in outputs:
            raise RenderError(f"{source}: duplicate output {relpath}")
        if isinstance(text, str):
            text = text.replace("\r\n", "\n")
            if header and relpath.endswith((".yaml", ".yml")):
                text = f"# Rendered by tools/render from {source}. Do not edit.\n" + text
            if not text.endswith("\n"):
                text += "\n"
            text = text.encode("utf-8")
        elif relpath.endswith((".yaml", ".yml")):
            # Preserve copied bytes while giving every YAML output its provenance header.
            text = f"# Rendered by tools/render from {source}. Do not edit.\n".encode("utf-8") + text
        outputs[relpath] = text
        if relpath.endswith(".yaml") and not relpath.startswith("files/"):
            decoded = text.decode("utf-8")
            groups = None
            try:
                document = json.loads(decoded.split("\n", 1)[1] if decoded.startswith("#") else decoded)
            except (ValueError, TypeError):
                document = None
            if isinstance(document, dict) and document.get("kind") == "PrometheusRule":
                groups = json.dumps({"groups": document["spec"]["groups"]}, sort_keys=True, indent=2) + "\n"
            elif "kind: PrometheusRule\n" in decoded:
                rules = [part for part in re.split(r"(?m)^---\s*$", decoded)
                         if "kind: PrometheusRule\n" in part and "spec:\n" in part]
                if len(rules) == 1:
                    body = rules[0].split("spec:\n", 1)[1]
                    groups = "\n".join(line[2:] if line.startswith("  ") else line
                                       for line in body.splitlines()) + "\n"
            if groups is not None:
                parent, filename = relpath.rsplit("/", 1)
                plain = "files/" + parent.split("/", 1)[1] + "/rules/" + filename
                if plain in outputs:
                    raise RenderError(f"{source}: duplicate output {plain}")
                outputs[plain] = groups.encode("utf-8")

    render_templates(root, model, emit, exclude)
    render_plugins(root, model, emit, exclude)
    render_secrets(root, model, emit, exclude)
    outputs[MARKER] = b"tools/render\n"
    return outputs


def validate_output(out, root):
    if out.is_symlink() or out == root or out in root.parents:
        raise RenderError(f"{out}: unsafe output directory")
    # Symlink ancestors could redirect writes outside the chosen directory.
    if any(parent.is_symlink() for parent in [out, *out.parents]):
        raise RenderError(f"{out}: symlink output directory")
    if out.exists():
        if not out.is_dir():
            raise RenderError(f"{out}: output must be a directory")
        if any(out.iterdir()) and not (out / MARKER).is_file():
            raise RenderError(f"{out}: nonempty output requires {MARKER}")
        if (out / MARKER).is_symlink():
            raise RenderError(f"{out}: symlink marker refused")
        for path in out.rglob("*"):
            if path.is_symlink():
                raise RenderError(f"{path}: symlink in output refused")


def write_output(out, outputs):
    out.mkdir(parents=True, exist_ok=True)
    for path in sorted(out.iterdir()):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    for relpath, data in sorted(outputs.items()):
        path = out / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def list_keys(sets, scope, markdown):
    if scope is not None and scope not in sets:
        raise RenderError(f"--scope: unknown scope {scope}")
    groups = [scope] if scope else list(sets)
    for group in groups:
        keys = sets[group] | (sets["global"] if scope and group != "global" else set())
        if markdown and group == "global" and scope is None:
            covered = set()
            for title, names in GLOBAL_GROUPS.items():
                selected = [k for k in names if k in keys]
                if title == "modules":
                    selected = sorted(k for k in keys if k.startswith("M_"))
                if title == "components":
                    selected = sorted(k for k in keys if k.startswith("C_"))
                covered.update(selected)
                print(f"### Global: {title}\n")
                print(
                    " ".join(f"`{key}`" for key in selected)
                    or "Computed from merged component defaults as `C_<COMPONENT>_<KEY>`; lists append `_JSON`."
                )
                print()
            remaining = keys - covered
            if remaining:
                print("### Global: additional defaults\n")
                print(" ".join(f"`{key}`" for key in sorted(remaining)))
                print()
        elif markdown:
            print(f"### {group} placeholders\n")
            print(" ".join(f"`{key}`" for key in sorted(keys)))
            print()
        else:
            for key in sorted(keys):
                print(key)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Render org configuration into committed manifests.")
    parser.add_argument("--org", default="org/org.yaml")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--out", type=Path, default=Path("rendered"))
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--print-model", action="store_true")
    parser.add_argument("--list-keys", action="store_true")
    parser.add_argument("--scope")
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument("--lint-placeholders", nargs="+")
    args = parser.parse_args(argv)
    root = args.root.absolute()
    org = Path(args.org)
    org = org if org.is_absolute() else root / org
    out = args.out if args.out.is_absolute() else root / args.out
    out = out.absolute()
    try:
        if (args.scope or args.markdown) and not args.list_keys:
            raise RenderError("--scope and --markdown require --list-keys")
        if not org.exists():
            if args.lint_placeholders and args.org == "org/org.yaml":
                org = root / "org/org.example.yaml"
        if not org.exists():
            if args.list_keys and args.org == "org/org.yaml":
                list_keys({k: set(v) for k, v in KEY_NAMES.items()}, args.scope, args.markdown)
                return 0
            raise OrgError(
                f"{org}: missing configuration; copy org/org.example.yaml to org/org.yaml and edit it"
            )
        model = load_model(root, org, args.strict)
        if args.list_keys:
            list_keys(key_sets(model), args.scope, args.markdown)
            return 0
        if args.print_model:
            print(json.dumps(model, indent=2, sort_keys=True))
            return 0
        for warning in model["warnings"]:
            print(f"warning: {warning}", file=sys.stderr)
        if args.lint_placeholders:
            errors = lint(args.lint_placeholders, model)
            for error in errors:
                print(error, file=sys.stderr)
            return 1 if errors else 0
        if args.validate_only:
            return 0
        validate_output(out, root)
        outputs = render(root, model, exclude=[out])
        forbidden = [path for path in outputs if path.startswith(("users/", "mcp/", "global/"))
                     and (Path(path).name in ("kustomization.yaml", "Chart.yaml")
                          or Path(path).name.startswith("values.") or Path(path).name == "values.yaml")]
        if forbidden:
            raise RenderError("manifest-only Argo trees contain chart/kustomize inputs: " + ", ".join(forbidden))
        if args.check:
            with tempfile.TemporaryDirectory(prefix="aa-render-check-") as temporary:
                temp = Path(temporary)
                write_output(temp, outputs)
                actual = {
                    p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*") if p.is_file()
                }
                drift = sorted(k for k in outputs.keys() | actual.keys() if outputs.get(k) != actual.get(k))
                for path in drift:
                    print(f"drift: {path}")
                return 1 if drift else 0
        write_output(out, outputs)
        return 0
    except (OrgError, RenderError, YamlError, OSError, UnicodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

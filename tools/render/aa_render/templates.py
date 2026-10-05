"""Deterministic discovery, gating, substitution and template paths."""

from __future__ import annotations

import os
import re
from pathlib import Path

from .yamlsub import parse_yaml_subset

SKIP = {
    ".git",
    "rendered",
    "tests",
    "fixtures",
    "docs",
    ".ci-venvs",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
}
KEY_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")
TEMPLATE_RE = re.compile(r"^(.*?)(?:\.per-([a-z-]+))?\.tmpl\.(.+)$")
SCOPES = {"user", "user-tool", "team", "node", "mcp", "account"}


class RenderError(ValueError):
    pass


def lookup(model, dotted):
    value = model
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def condition(text, model):
    match = re.fullmatch(r"\s*([A-Za-z0-9_.-]+)(?:\s+(==|!=)\s+(.+?))?\s*", text)
    if not match:
        raise RenderError(f"invalid RENDER-IF condition: {text!r}")
    dotted, op, expected = match.groups()
    actual = lookup(model, dotted)
    if op is None:
        return bool(actual)
    # Parse a scalar through the same typing rules as configuration.
    expected = parse_yaml_subset("value: " + expected)["value"]
    if actual is None:
        return False
    from .model import s
    return s(actual) == s(expected) if op == "==" else s(actual) != s(expected)


def walk(root, model=None, gates=True, exclude=()):
    root = Path(root).resolve()
    excluded = {Path(p).resolve() for p in exclude}
    for directory, dirs, names in os.walk(root, followlinks=False):
        directory = Path(directory)
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in SKIP
            and not (directory / d).is_symlink()
            and (directory / d).resolve() not in excluded
        )
        rel = directory.relative_to(root)
        if gates and model is not None:
            if len(rel.parts) >= 2 and rel.parts[0] == "modules":
                if not model["org"].get("modules", {}).get(rel.parts[1], {}).get("enabled", False):
                    dirs[:] = []
                    continue
            if "RENDER-IF" in names:
                path = directory / "RENDER-IF"
                try:
                    enabled = condition(path.read_text(encoding="utf-8").strip(), model)
                except ValueError as exc:
                    raise RenderError(f"{path}: {exc}") from exc
                if not enabled:
                    dirs[:] = []
                    continue
        for name in sorted(names):
            path = directory / name
            if not path.is_symlink():
                yield path


def subst(text, keys, source="<template>"):
    def replace(match):
        key = match.group(1)
        if key not in keys:
            raise RenderError(f"{source}: unknown placeholder {key}")
        return keys[key]

    return KEY_RE.sub(replace, text)


def render_templates(root, model, emit, exclude=()):
    for path in walk(root, model, exclude=exclude):
        rel = path.relative_to(root)
        match = TEMPLATE_RE.fullmatch(path.name)
        if not match:
            if any(segment in ("k8s", "helm") for segment in rel.parts[:-1]):
                prefix = "global" if "k8s" in rel.parts[:-1] else "files"
                emit(f"{prefix}/{rel.as_posix()}", path.read_bytes(), rel.as_posix(), header=False)
            continue
        base, scope, ext = match.groups()
        name = f"{base}.{ext}"
        parent = rel.parent.as_posix()
        text = path.read_text(encoding="utf-8")
        if scope is None:
            prefix = "global" if "k8s" in rel.parts[:-1] else "files"
            emit(f"{prefix}/{parent}/{name}", subst(text, model["keys"], rel), rel.as_posix())
            continue
        if scope not in SCOPES:
            raise RenderError(f"{rel}: unknown scope {scope}")
        for entity in model["entities"][scope.replace("-", "_")]:
            if scope == "user-tool":
                filters = set(rel.parts[:-1]) & {"claude", "codex", "kimi"}
                if filters and entity["TOOL"] not in filters:
                    continue
                prefix = f"users/{entity['USER_SLUG']}/{parent}/{entity['TOOL']}"
            else:
                group = {
                    "user": "users",
                    "team": "teams",
                    "node": "nodes",
                    "mcp": "mcp",
                    "account": "accounts",
                }[scope]
                prefix = f"{group}/{entity['ENTITY_ID']}/{parent}"
            emit(f"{prefix}/{name}", subst(text, {**model["keys"], **entity}, rel), rel.as_posix())

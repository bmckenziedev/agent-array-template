"""Discover trusted local plugins and constrain every emitted output path."""

from __future__ import annotations

import importlib.util
from pathlib import PurePosixPath, PureWindowsPath

from .templates import RenderError, walk


def safe_path(relpath):
    if not isinstance(relpath, str) or not relpath or "\\" in relpath:
        raise RenderError(f"invalid emit path {relpath!r}")
    path = PurePosixPath(relpath)
    if (
        path.is_absolute()
        or PureWindowsPath(relpath).drive
        or ".." in path.parts
        or "." in relpath.split("/")
    ):
        raise RenderError(f"unsafe emit path {relpath!r}")
    return path


def render_plugins(root, model, emit, exclude=()):
    names = set()
    for index, path in enumerate(walk(root, model, exclude=exclude)):
        if path.name != "render_plugin.py":
            continue
        directory = path.parent.relative_to(root).as_posix()
        spec = importlib.util.spec_from_file_location(f"aa_render_plugin_{index}", path)
        module = importlib.util.module_from_spec(spec)
        # Executing source avoids generating bytecode in component directories.
        try:
            exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
        except Exception as exc:
            raise RenderError(f"{path}: cannot load plugin: {exc}") from exc
        name = getattr(module, "PLUGIN_NAME", None)
        if not isinstance(name, str) or not name or name in names:
            raise RenderError(f"{path}: PLUGIN_NAME must be unique and nonempty")
        names.add(name)
        prefixes = [f"global/{directory}/", f"files/{directory}/"]
        for scope, group, key in (
            ("user", "users", "USER_SLUG"),
            ("team", "teams", "TEAM_ID"),
            ("mcp", "mcp", "MCP_NAME"),
        ):
            prefixes.extend(f"{group}/{e[key]}/{directory}/" for e in model["entities"][scope])

        def guarded(relpath, text):
            safe_path(relpath)
            if not any(relpath.startswith(prefix) for prefix in prefixes):
                raise RenderError(f"{path}: emit path outside plugin directory: {relpath}")
            if not isinstance(text, str):
                raise RenderError(f"{path}: plugin output must be text")
            emit(relpath, text, path.relative_to(root).as_posix())

        try:
            module.render(model, guarded)
        except Exception as exc:
            raise RenderError(f"{path}: {exc}") from exc

"""Check literal placeholder tokens without changing source files."""

import os
from pathlib import Path

from .key_names import KEY_NAMES
from .templates import KEY_RE, TEMPLATE_RE, SCOPES


def key_sets(model):
    result = {name: set(keys) for name, keys in KEY_NAMES.items()}
    result["global"] = set(model["keys"])
    for scope in sorted(SCOPES):
        result[scope].update(key for entity in model["entities"][scope.replace("-", "_")] for key in entity)
    return result


def lint(paths, model):
    sets = key_sets(model)
    all_keys = set().union(*sets.values())
    errors = []
    skipped = {".git", "rendered", ".ci-venvs", "__pycache__", "node_modules", ".venv", "venv", "tests", "fixtures", "docs"}
    files = set()
    for requested in paths:
        path = Path(requested)
        if not path.exists():
            errors.append(f"{path}: path does not exist")
            continue
        if path.is_file():
            files.add(path)
        else:
            for directory, dirs, names in os.walk(path):
                dirs[:] = sorted(
                    d for d in dirs if d not in skipped and not (Path(directory) / d).is_symlink()
                )
                files.update(Path(directory) / name for name in names)
    for path in sorted(files):
        if path.is_symlink() or any(p in skipped for p in path.parts):
            continue
        data = path.read_bytes()
        if b"\x00" in data:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeError:
            continue
        allowed = all_keys
        match = TEMPLATE_RE.fullmatch(path.name)
        if not match and (path.suffix not in {".yaml", ".yml", ".json", ".env", ".sh", ".toml"}
                          or not {"k8s", "helm"}.intersection(path.parts[:-1])):
            continue
        if match:
            scope = match.group(2)
            if scope and scope not in sets:
                errors.append(f"{path}: unknown scope {scope}")
                continue
            allowed = sets["global"] | sets.get(scope, set())
        for number, line in enumerate(text.splitlines(), 1):
            for token in KEY_RE.finditer(line):
                if token.group(1) not in allowed:
                    errors.append(f"{path}:{number}: unknown placeholder {token.group(1)}")
    return errors

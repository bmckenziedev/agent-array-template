"""JSON Schema validation (draft 2020-12 subset), stdlib only.

The schemas in factory/engine/schemas/ are ordinary draft 2020-12 documents, so any full validator
(e.g. the `jsonschema` package; tests cross-check with it when it is installed) accepts them. This
module implements exactly the keywords those schemas use, and refuses to load a schema that uses
any other keyword, so a schema edit can never silently stop being enforced:

  $schema $id $defs $ref(local) title description $comment examples default
  type enum const
  properties required additionalProperties patternProperties propertyNames minProperties maxProperties
  items minItems maxItems uniqueItems
  minLength maxLength pattern
  minimum maximum exclusiveMinimum exclusiveMaximum
  allOf anyOf oneOf not if/then/else
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from .config import SCHEMA_DIR

KNOWN = {
    "$schema", "$id", "$defs", "$ref", "title", "description", "$comment", "examples", "default",
    "type", "enum", "const",
    "properties", "required", "additionalProperties", "patternProperties", "propertyNames",
    "minProperties", "maxProperties",
    "items", "minItems", "maxItems", "uniqueItems",
    "minLength", "maxLength", "pattern",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "allOf", "anyOf", "oneOf", "not", "if", "then", "else",
}
_SUBSCHEMA_MAPS = ("properties", "patternProperties", "$defs")
_SUBSCHEMA_ONE = ("additionalProperties", "items", "not", "if", "then", "else", "propertyNames")
_SUBSCHEMA_LIST = ("allOf", "anyOf", "oneOf")


class SchemaError(ValueError):
    """The input does not satisfy the schema. `errors` lists `path: message` strings."""

    def __init__(self, what: str, errors: list[str]):
        self.errors = errors
        shown = "; ".join(errors[:8]) + (f" (+{len(errors) - 8} more)" if len(errors) > 8 else "")
        super().__init__(f"{what}: {shown}")


def _check_keywords(schema, where: str = "#") -> None:
    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict):
        raise ValueError(f"schema at {where} is not an object")
    unknown = set(schema) - KNOWN
    if unknown:
        raise ValueError(f"schema at {where} uses unsupported keyword(s) {sorted(unknown)}")
    for k in _SUBSCHEMA_MAPS:
        for name, sub in (schema.get(k) or {}).items():
            _check_keywords(sub, f"{where}/{k}/{name}")
    for k in _SUBSCHEMA_ONE:
        if k in schema and not isinstance(schema[k], bool):
            _check_keywords(schema[k], f"{where}/{k}")
    for k in _SUBSCHEMA_LIST:
        for i, sub in enumerate(schema.get(k) or []):
            _check_keywords(sub, f"{where}/{k}/{i}")


@lru_cache(maxsize=None)
def load(name: str) -> dict:
    """Load schemas/<name>.schema.json (e.g. 'card.v1') and check it only uses supported keywords."""
    path = SCHEMA_DIR / f"{name}.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    _check_keywords(schema)
    return schema


@lru_cache(maxsize=256)
def _ecma(pattern: str) -> "re.Pattern[str]":
    r"""Compile a JSON Schema (ECMA-262) pattern for Python's re. ECMA `$` (no m flag) matches only at
    the very end; Python's `$` also matches before a trailing newline, so "id\n" would pass `^id$`.
    Every unescaped `$` outside a character class becomes `\Z`."""
    out, i, in_class = [], 0, False
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            out.append(pattern[i:i + 2])
            i += 2
            continue
        if ch == "[" and not in_class:
            in_class = True
        elif ch == "]" and in_class:
            in_class = False
        elif ch == "$" and not in_class:
            ch = r"\Z"
        out.append(ch)
        i += 1
    return re.compile("".join(out))


def _type_ok(value, t: str) -> bool:
    if t == "object":
        return isinstance(value, dict)
    if t == "array":
        return isinstance(value, list)
    if t == "string":
        return isinstance(value, str)
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "boolean":
        return isinstance(value, bool)
    if t == "null":
        return value is None
    raise ValueError(f"unknown type {t}")


def _eq(a, b) -> bool:
    # JSON equality: 1 == 1.0 but True != 1
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_eq(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_eq(a[k], b[k]) for k in a)
    return type(a) is type(b) and a == b


def _resolve(root: dict, ref: str) -> dict:
    if not ref.startswith("#/"):
        raise ValueError(f"only local $ref is supported, got {ref}")
    node = root
    for part in ref[2:].split("/"):
        node = node[part.replace("~1", "/").replace("~0", "~")]
    return node


def _validate(value, schema, root: dict, path: str, errors: list[str]) -> None:
    if schema is True:
        return
    if schema is False:
        errors.append(f"{path or '/'}: not allowed")
        return
    if "$ref" in schema:
        _validate(value, _resolve(root, schema["$ref"]), root, path, errors)
    p = path or "/"
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_ok(value, t) for t in types):
            errors.append(f"{p}: expected {' or '.join(types)}, got {type(value).__name__}")
            return
    if "const" in schema and not _eq(value, schema["const"]):
        errors.append(f"{p}: must be {json.dumps(schema['const'])}")
    if "enum" in schema and not any(_eq(value, e) for e in schema["enum"]):
        errors.append(f"{p}: must be one of {', '.join(json.dumps(e) for e in schema['enum'])}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{p}: shorter than {schema['minLength']} characters")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{p}: longer than {schema['maxLength']} characters")
        if "pattern" in schema and not _ecma(schema["pattern"]).search(value):
            errors.append(f"{p}: does not match {schema['pattern']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{p}: below the minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{p}: above the maximum {schema['maximum']}")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            errors.append(f"{p}: must be above {schema['exclusiveMinimum']}")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            errors.append(f"{p}: must be below {schema['exclusiveMaximum']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{p}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{p}: more than {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            for i in range(len(value)):
                if any(_eq(value[i], value[j]) for j in range(i)):
                    errors.append(f"{p}: items are not unique ({json.dumps(value[i])[:60]})")
                    break
        if "items" in schema:
            for i, item in enumerate(value):
                _validate(item, schema["items"], root, f"{path}/{i}", errors)
    if isinstance(value, dict):
        for req in schema.get("required", []):
            if req not in value:
                errors.append(f"{p}: missing required property '{req}'")
        if "minProperties" in schema and len(value) < schema["minProperties"]:
            errors.append(f"{p}: fewer than {schema['minProperties']} properties")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            errors.append(f"{p}: more than {schema['maxProperties']} properties")
        props = schema.get("properties", {})
        pats = schema.get("patternProperties", {})
        for k, v in value.items():
            if "propertyNames" in schema:
                _validate(k, schema["propertyNames"], root, f"{path}/{k}", errors)
            matched = False
            if k in props:
                matched = True
                _validate(v, props[k], root, f"{path}/{k}", errors)
            for pat, sub in pats.items():
                if _ecma(pat).search(k):
                    matched = True
                    _validate(v, sub, root, f"{path}/{k}", errors)
            if not matched and "additionalProperties" in schema:
                ap = schema["additionalProperties"]
                if ap is False:
                    errors.append(f"{p}: unexpected property '{k}'")
                elif ap is not True:
                    _validate(v, ap, root, f"{path}/{k}", errors)
    for sub in schema.get("allOf", []):
        _validate(value, sub, root, path, errors)
    if "anyOf" in schema:
        if not any(not _collect(value, s, root, path) for s in schema["anyOf"]):
            errors.append(f"{p}: does not match any allowed form")
    if "oneOf" in schema:
        results = [_collect(value, s, root, path) for s in schema["oneOf"]]
        n = sum(1 for r in results if not r)
        if n != 1:
            if n == 0:
                best = min(results, key=len)
                errors.append(f"{p}: matches none of the allowed forms (closest: {'; '.join(best[:2])})")
            else:
                errors.append(f"{p}: matches more than one allowed form")
    if "not" in schema and not _collect(value, schema["not"], root, path):
        errors.append(f"{p}: matches a form that is not allowed")
    if "if" in schema:
        if not _collect(value, schema["if"], root, path):
            if "then" in schema:
                _validate(value, schema["then"], root, path, errors)
        elif "else" in schema:
            _validate(value, schema["else"], root, path, errors)


def _collect(value, schema, root, path) -> list[str]:
    errs: list[str] = []
    _validate(value, schema, root, path, errs)
    return errs


def errors(name: str, value) -> list[str]:
    schema = load(name)
    return _collect(value, schema, schema, "")


def validate_or_raise(name: str, value, what: str = "") -> None:
    errs = errors(name, value)
    if errs:
        raise SchemaError(what or name, errs)


def schema_path(name: str) -> Path:
    return SCHEMA_DIR / f"{name}.schema.json"

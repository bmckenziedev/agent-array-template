"""Strict block YAML with scalar-only flow collections and located errors."""

from __future__ import annotations

import re
from pathlib import Path


class YamlError(ValueError):
    pass


class Parser:
    def __init__(self, text: str, source: str):
        self.source = source
        self.lines = []
        for number, raw in enumerate(text.splitlines(), 1):
            if "\t" in raw:
                self.fail(number, raw.index("\t") + 1, "tabs are unsupported")
            body = self.comment(raw, number).rstrip()
            if body.strip():
                indent = len(body) - len(body.lstrip(" "))
                self.lines.append((number, indent, body[indent:]))

    def fail(self, line, column, message):
        raise YamlError(f"{self.source}:{line}:{column}: {message}")

    def split(self, text, delimiter, line, column, flow=False):
        parts, start, quote, depth, i = [], 0, None, 0, 0
        while i < len(text):
            ch = text[i]
            if quote:
                if quote == '"' and ch == "\\":
                    i += 2
                    continue
                if ch == quote:
                    if quote == "'" and i + 1 < len(text) and text[i + 1] == "'":
                        i += 2
                        continue
                    quote = None
            elif ch in "\"'" and (i == start or text[i - 1] in " :,[{"):
                quote = ch
            elif ch in "[{":
                depth += 1
                if flow:
                    self.fail(line, column + i, "nested flow collections are unsupported")
            elif ch in "]}":
                depth -= 1
            elif ch == delimiter and depth == 0:
                parts.append(text[start:i])
                start = i + 1
            i += 1
        if quote or depth:
            self.fail(line, column, "unterminated quote or flow collection")
        parts.append(text[start:])
        return parts

    def comment(self, text, line):
        quote, i = None, 0
        while i < len(text):
            ch = text[i]
            if quote:
                if quote == '"' and ch == "\\":
                    i += 2
                    continue
                if ch == quote:
                    if quote == "'" and i + 1 < len(text) and text[i + 1] == "'":
                        i += 2
                        continue
                    quote = None
            elif ch in "\"'" and (i == 0 or text[i - 1] in " :,[{"):
                quote = ch
            elif ch == "#" and (i == 0 or text[i - 1].isspace()):
                return text[:i]
            i += 1
        if quote:
            self.fail(line, len(text), "multi-line or unterminated quoted scalar")
        return text

    def scalar(self, text, line, column):
        text = text.strip()
        if text.startswith(('"', "'")):
            quote = text[0]
            if len(text) < 2 or text[-1] != quote:
                self.fail(line, column, "unterminated quoted scalar")
            inner = text[1:-1]
            if quote == "'":
                if "'" in inner.replace("''", ""):
                    self.fail(line, column, "invalid single quote")
                return inner.replace("''", "'")
            result, i = [], 0
            escapes = {'"': '"', "\\": "\\", "n": "\n", "t": "\t"}
            while i < len(inner):
                if inner[i] == "\\":
                    i += 1
                    if i == len(inner) or inner[i] not in escapes:
                        self.fail(line, column + i, "unsupported double-quote escape")
                    result.append(escapes[inner[i]])
                elif inner[i] == '"':
                    self.fail(line, column + i, "unexpected quote")
                else:
                    result.append(inner[i])
                i += 1
            return "".join(result)
        if text.startswith(("[", "{")):
            close = "]" if text[0] == "[" else "}"
            if not text.endswith(close):
                self.fail(line, column, "unterminated flow collection")
            inner = text[1:-1].strip()
            items = self.split(inner, ",", line, column + 1, flow=True) if inner else []
            if items and not items[-1].strip():
                items.pop()
            if text[0] == "[":
                return [self.scalar(x, line, column + 1) for x in items]
            result = {}
            for item in items:
                key, value = self.pair(item.strip(), line, column + 1, flow=True)
                if key in result:
                    self.fail(line, column, f"duplicate key {key!r}")
                result[key] = self.scalar(value, line, column + 1)
            return result
        if text[:1] in ("&", "*", "!", "|", ">", "@", "`", "%"):
            self.fail(line, column, "anchors, aliases, tags and multi-line scalars are unsupported")
        if re.search(r"(?:^|\s)[&*!][A-Za-z]", text):
            self.fail(line, column, "anchors, aliases and tags are unsupported")
        if text.lower() in ("yes", "no", "on", "off"):
            self.fail(line, column, "quote ambiguous boolean")
        if re.fullmatch(
            r"[-+]?(?:[0-9]+\.[0-9]*|\.[0-9]+|[0-9]+[eE][-+]?[0-9]+)(?:[eE][-+]?[0-9]+)?", text
        ) or text.lower() in (".nan", ".inf", "-.inf", "+.inf"):
            self.fail(line, column, "quote floats")
        if re.match(r"^[-+]?[0-9]+(?::[0-9]{2})+(?:\.[0-9]+)?$", text):
            self.fail(line, column, "quote sexagesimal/time scalars")
        if re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}(?:$|[Tt ])", text):
            self.fail(line, column, "quote dates")
        if text in ("true", "false"):
            return text == "true"
        if text in ("null", "~", ""):
            return None
        if re.fullmatch(r"-?[0-9]+", text):
            return int(text)
        if re.search(r":(?:\s|$)", text) or text.startswith("- "):
            self.fail(line, column, "invalid plain scalar")
        return text

    def pair(self, text, line, column, flow=False):
        quote, i = None, 0
        while i < len(text):
            ch = text[i]
            if quote:
                if quote == '"' and ch == "\\":
                    i += 2
                    continue
                if ch == quote:
                    if quote == "'" and i + 1 < len(text) and text[i + 1] == "'":
                        i += 2
                        continue
                    quote = None
            elif ch in "\"'" and i == 0:
                quote = ch
            elif ch == ":" and (i + 1 == len(text) or text[i + 1].isspace()):
                key = self.scalar(text[:i], line, column)
                if not isinstance(key, str) or not key:
                    self.fail(line, column, "mapping key must be a string")
                return key, text[i + 1 :].strip()
            i += 1
        self.fail(line, column, "expected key: value")

    def node(self, i, indent, indentless=False):
        sequence = self.lines[i][2] == "-" or self.lines[i][2].startswith("- ")
        result = [] if sequence else {}
        while i < len(self.lines) and self.lines[i][1] == indent:
            number, _, text = self.lines[i]
            is_item = text == "-" or text.startswith("- ")
            if is_item != sequence:
                if sequence and indentless:
                    break
                self.fail(number, indent + 1, "mixed mapping and sequence")
            if sequence:
                rest = text[1:].strip()
                i += 1
                if not rest:
                    value, i = self.child(i, indent)
                elif not rest.startswith(("{", "[", '"', "'")) and re.search(r":(?:\s|$)", rest):
                    # The first mapping key in a list item is two columns after '-'.
                    item_indent = indent + len(text) - len(text[1:].lstrip())
                    key, tail = self.pair(rest, number, item_indent + 1)
                    value = {}
                    if tail:
                        value[key] = self.scalar(tail, number, item_indent + len(key) + 3)
                    else:
                        value[key], i = self.child(i, item_indent, allow_indentless=True)
                    if i < len(self.lines) and self.lines[i][1] == item_indent:
                        continuation = i
                        more, i = self.node(i, item_indent)
                        if not isinstance(more, dict):
                            self.fail(self.lines[i - 1][0], item_indent + 1, "expected mapping continuation")
                        for k in more:
                            if k in value:
                                duplicate_line = next(
                                    n
                                    for n, ind, body in self.lines[continuation:i]
                                    if ind == item_indent and self.pair(body, n, ind + 1)[0] == k
                                )
                                self.fail(duplicate_line, item_indent + 1, f"duplicate key {k!r}")
                        value.update(more)
                else:
                    value = self.scalar(rest, number, indent + len(text) - len(text[1:].lstrip()) + 1)
                result.append(value)
            else:
                key, rest = self.pair(text, number, indent + 1)
                if key in result:
                    self.fail(number, indent + 1, f"duplicate key {key!r}")
                i += 1
                if rest:
                    result[key] = self.scalar(rest, number, indent + len(key) + 3)
                else:
                    result[key], i = self.child(i, indent, allow_indentless=True)
            if i < len(self.lines) and self.lines[i][1] > indent:
                self.fail(self.lines[i][0], self.lines[i][1] + 1, "unexpected indentation")
        return result, i

    def child(self, i, indent, allow_indentless=False):
        if i < len(self.lines) and self.lines[i][1] > indent:
            return self.node(i, self.lines[i][1])
        if (
            allow_indentless
            and i < len(self.lines)
            and self.lines[i][1] == indent
            and (self.lines[i][2] == "-" or self.lines[i][2].startswith("- "))
        ):
            return self.node(i, indent, indentless=True)
        return None, i

    def parse(self):
        if not self.lines:
            return None
        if self.lines[0][1]:
            self.fail(self.lines[0][0], 1, "top level must not be indented")
        if len(self.lines) == 1 and self.lines[0][2].startswith(("{", "[")):
            return self.scalar(self.lines[0][2], self.lines[0][0], 1)
        result, i = self.node(0, 0)
        if i != len(self.lines):
            self.fail(self.lines[i][0], self.lines[i][1] + 1, "bad indentation")
        return result


def parse_yaml_subset(text: str, source: str = "<yaml>"):
    return Parser(text, source).parse()


def load(path: Path):
    return parse_yaml_subset(Path(path).read_text(encoding="utf-8"), str(path))

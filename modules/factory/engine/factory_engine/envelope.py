"""Output envelope (gate G0): an explicit marker or proven EOS closes a non-empty body.

The assistant turn is pre-filled with '<<<CODE\\n' and the stop sequence is '\\nCODE>>>'
(raw /v1/completions), or, for chat endpoints, the reply must open with a '<<<CODE' line and
close with a 'CODE>>>' line or EOS. Rejected: truncation (finish_reason=length),
unknown stop reasons, an empty body,
a markdown fence, a second opener, a marker that is not on its own line, text after the marker.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import OPEN, STOP

MARKER = STOP.strip()          # CODE>>>


@dataclass
class Envelope:
    ok: bool
    code: str
    reason: str | None = None
    stage: str = "envelope"


def _body_ok(body: str) -> Envelope:
    if OPEN in body:
        return Envelope(False, "", "a second <<<CODE opener inside the envelope")
    if MARKER in body:
        return Envelope(False, "", "CODE>>> marker not on its own line")
    stripped = body.strip()
    if not stripped:
        return Envelope(False, "", "empty envelope")
    if stripped.startswith("```"):
        return Envelope(False, "", "markdown fence inside the envelope")
    return Envelope(True, body.strip("\n") + "\n")


def parse_completion(text: str, stop: str) -> Envelope:
    """Raw-completion output. `stop` is marker | eos | length | unknown | error."""
    if stop == "error":
        return Envelope(False, "", "infra: request failed", stage="infra")
    if stop == "length":
        return Envelope(False, "", "truncated: max_tokens reached before CODE>>> (finish_reason=length)")
    if stop not in ("marker", "eos"):
        return Envelope(False, "", "the server cannot show that CODE>>> ended the output (stop reason unknown)")
    return _body_ok(text)


def parse_chat(content: str, finish_reason: str | None) -> Envelope:
    """Chat-completion output: the whole reply must be '<<<CODE\\n...\\nCODE>>>' (whitespace around ok)."""
    if finish_reason == "length":
        return Envelope(False, "", "truncated: max_tokens reached before CODE>>> (finish_reason=length)")
    text = (content or "").strip()
    if not text.startswith(OPEN):
        return Envelope(False, "", "reply does not start with the <<<CODE line")
    rest = text[len(OPEN):]
    if not rest.startswith("\n") and not rest.startswith("\r\n"):
        return Envelope(False, "", "<<<CODE must be alone on its first line")
    rest = rest.lstrip("\r").lstrip("\n")
    idx = rest.rfind("\n" + MARKER)
    if idx < 0:
        if rest.startswith(MARKER):
            return Envelope(False, "", "empty envelope")
        if finish_reason == "stop":
            return _body_ok(rest.replace("\r\n", "\n"))
        return Envelope(False, "", "ended without the CODE>>> marker")
    body, tail = rest[:idx], rest[idx + 1 + len(MARKER):]
    if tail.strip():
        return Envelope(False, "", "text after the CODE>>> marker")
    return _body_ok(body.replace("\r\n", "\n"))

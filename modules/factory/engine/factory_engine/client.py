"""Model endpoint clients: raw /v1/completions (vLLM, any OpenAI-compatible server), llama.cpp's
native /completion, and /v1/chat/completions with the strict envelope.

Stdlib HTTP (urllib) run in worker threads, so the asyncio lane semaphores bound concurrency.
Bearer keys come from key_file or key_env at request time; they are never logged, stored or
returned. Every request has a timeout (bounded waits).
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .envelope import Envelope, parse_chat, parse_completion
from .packer import render_prompt


@dataclass
class Generation:
    text: str
    stop: str                      # marker | eos | length | unknown | error
    envelope: Envelope
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_s: float
    server: dict = field(default_factory=dict)
    error: str | None = None       # infra error text (never contains the key)


class InfraError(Exception):
    """Endpoint unreachable / 5xx / timeout: an infrastructure failure, never a model failure."""


def _key(ep: dict) -> str | None:
    if ep.get("key_env"):
        v = os.environ.get(ep["key_env"])
        return v.strip() if v else None
    if ep.get("key_file"):
        try:
            return Path(ep["key_file"]).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise InfraError(f"cannot read the lane's key_file ({type(exc).__name__})") from None
    return None


def _post(url: str, body: dict, key: str | None, timeout: int) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        if e.code == 400:
            e.close()
            raise
        e.close()
        raise InfraError(f"HTTP {e.code} from {url.split('?')[0]}") from None
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        raise InfraError(f"{type(e).__name__}: {str(getattr(e, 'reason', e))[:200]}") from None


def get_json(url: str, key: str | None = None, timeout: int = 10) -> dict:
    req = urllib.request.Request(url, method="GET")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def _v1(base: str) -> str:
    base = base.rstrip("/")
    return base if base.endswith("/v1") else base + "/v1"


def _root(base: str) -> str:
    base = base.rstrip("/")
    return base[:-3] if base.endswith("/v1") else base


def generate(lane: dict, system: str, user: str, max_tokens: int, rung: dict, seed: int) -> Generation:
    """One generation (n=1). Raises InfraError for endpoint failures."""
    ep = lane["endpoint"]
    api = ep.get("api", "openai-completions")
    timeout = int(ep.get("timeout_s", 300))
    key = _key(ep)
    samp = dict(config.DEFAULT_SAMPLING)
    samp.update({k: v for k, v in rung.items() if k in ("temperature", "top_p", "top_k", "min_p",
                                                        "repetition_penalty", "presence_penalty")})
    temp = float(samp.get("temperature", 0.2))
    t0 = time.perf_counter()
    extra = ep.get("extra") or {}
    if api == "llamacpp":
        body = {"prompt": render_prompt(ep, system, user), "n_predict": max_tokens, "temperature": temp,
                "top_p": samp["top_p"], "top_k": samp["top_k"], "min_p": samp.get("min_p", 0.0),
                "repeat_penalty": samp.get("repetition_penalty", 1.0),
                "presence_penalty": samp.get("presence_penalty", 0.0), "seed": seed,
                "stop": [config.STOP], "cache_prompt": True, "stream": False, **extra}
        r = _post(_root(ep["base_url"]) + "/completion", body, key, timeout)
        dt = time.perf_counter() - t0
        st = r.get("stop_type")
        if st is None:
            st = "word" if r.get("stopped_word") else ("eos" if r.get("stopped_eos") else ("limit" if r.get("stopped_limit") else None))
        stop = {"word": "marker", "eos": "eos", "limit": "length"}.get(st, "unknown")
        if stop == "marker" and r.get("stopping_word") not in (None, "", config.STOP):
            stop = "unknown"
        if r.get("truncated"):
            stop = "length"
        text = r.get("content", "")
        t = r.get("timings") or {}
        return Generation(text, stop, parse_completion(text, stop), r.get("tokens_evaluated") or t.get("prompt_n"),
                          r.get("tokens_predicted") or t.get("predicted_n"), dt,
                          {"stop_type": st, "cache_n": t.get("cache_n")})
    if api == "openai-completions":
        body = {"model": ep.get("model"), "prompt": render_prompt(ep, system, user), "max_tokens": max_tokens,
                "temperature": temp, "top_p": samp["top_p"], "seed": seed, "stop": [config.STOP], "n": 1,
                "presence_penalty": samp.get("presence_penalty", 0.0),
                "top_k": samp["top_k"], "min_p": samp.get("min_p", 0.0),
                "repetition_penalty": samp.get("repetition_penalty", 1.0),
                "include_stop_str_in_output": True, "skip_special_tokens": True, **extra}
        try:
            r = _post(_v1(ep["base_url"]) + "/completions", body, key, timeout)
        except urllib.error.HTTPError:
            # a strict OpenAI server rejects the vLLM extras: retry with the plain fields
            for k in ("top_k", "min_p", "repetition_penalty", "include_stop_str_in_output", "skip_special_tokens"):
                body.pop(k, None)
            try:
                r = _post(_v1(ep["base_url"]) + "/completions", body, key, timeout)
            except urllib.error.HTTPError as e:
                raise InfraError(f"HTTP {e.code} from the completions endpoint") from None
        dt = time.perf_counter() - t0
        ch = (r.get("choices") or [{}])[0]
        text = ch.get("text", "")
        fr, sr = ch.get("finish_reason"), ch.get("stop_reason")
        if fr == "length":
            stop = "length"
        elif text.endswith(config.STOP) or sr == config.STOP:
            stop = "marker"
            text = text[: -len(config.STOP)] if text.endswith(config.STOP) else text
        elif fr == "stop" and sr is None and body.get("include_stop_str_in_output"):
            stop = "eos"
        elif fr == "stop" and ep.get("accept_unknown_stop"):
            stop = "marker"
        else:
            stop = "unknown"
        u = r.get("usage") or {}
        return Generation(text, stop, parse_completion(text, stop), u.get("prompt_tokens"), u.get("completion_tokens"),
                          dt, {"finish_reason": fr, "stop_reason": sr})
    if api == "openai-chat":
        instr = (f"\n\nStart your answer with the line {config.OPEN} and end it with the line "
                 f"{config.STOP.strip()} . Nothing before or after.")
        body = {"model": ep.get("model"), "messages": [{"role": "system", "content": system},
                                                       {"role": "user", "content": user + instr}],
                "max_tokens": max_tokens, "temperature": temp, "top_p": samp["top_p"], "seed": seed, "n": 1,
                "presence_penalty": samp.get("presence_penalty", 0.0), **extra}
        try:
            r = _post(_v1(ep["base_url"]) + "/chat/completions", body, key, timeout)
        except urllib.error.HTTPError as e:
            raise InfraError(f"HTTP {e.code} from the chat endpoint") from None
        dt = time.perf_counter() - t0
        ch = (r.get("choices") or [{}])[0]
        content = (ch.get("message") or {}).get("content") or ""
        fr = ch.get("finish_reason")
        env = parse_chat(content, fr)
        stop = "length" if fr == "length" else ("marker" if env.ok else "eos")
        u = r.get("usage") or {}
        return Generation(content, stop, env, u.get("prompt_tokens"), u.get("completion_tokens"), dt, {"finish_reason": fr})
    raise ValueError(f"unknown api {api}")


def probe(lane: dict, timeout: int = 10) -> dict:
    """Reachability: /health (no auth) and /v1/models (auth). Never returns the key."""
    ep = lane["endpoint"]
    out = {"base_url": ep["base_url"], "model": ep.get("model")}
    try:
        get_json(_root(ep["base_url"]) + "/health", None, timeout)
        out["health"] = "ok"
    except urllib.error.HTTPError as e:
        out["health"] = f"http {e.code}"
    except Exception as e:  # noqa: BLE001
        out["health"] = f"down ({type(e).__name__})"
    try:
        r = get_json(_v1(ep["base_url"]) + "/models", _key(ep), timeout)
        ids = [d.get("id") for d in r.get("data", [])]
        out["models"] = ids
        out["served_ok"] = (ep.get("model") in ids) or not ids
    except Exception as e:  # noqa: BLE001
        out["models"] = f"error ({type(e).__name__})"
        out["served_ok"] = False
    out["ok"] = out["health"] == "ok" and out.get("served_ok") is True
    return out

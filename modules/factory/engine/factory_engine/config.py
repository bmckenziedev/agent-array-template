"""Paths, defaults and feature flags.

Every default here is overridable by a lanes file (`--lanes`, schema schemas/lanes.v1.schema.json)
or an environment variable. Nothing in this module reads a secret.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent          # factory/engine
SCHEMA_DIR = ENGINE_DIR / "schemas"
JS_DIR = ENGINE_DIR / "js"
PROFILES_DIR = ENGINE_DIR / "profiles"
DOCKER_DIR = ENGINE_DIR / "docker"

# Output envelope (design: MODEL OUTPUT CONTRACT). The assistant turn is pre-filled with OPEN + "\n";
# the stop sequence is STOP. finish_reason=length (truncation) is always rejected.
OPEN = "<<<CODE"
STOP = "\nCODE>>>"

CTX_MARGIN = 256                 # prompt + max_tokens <= ctx - CTX_MARGIN
MAX_GENERATIONS = 3              # corrected plan: at most 3 generations per unit
FEEDBACK_TOKENS = 600            # retry feedback budget
BASE_SEED = 20261004

# Kinds the engine implements. doc_map is always on; test_gen sits behind a feature flag because its
# gate executes model-written tests (only ever inside an isolated runner).
IMPLEMENTED_KINDS = ("doc_map", "test_gen")
FLAG_TEST_GEN = "test_gen"

# Output reserve per kind (max_tokens), bounded by the lane's context.
MAX_TOKENS = {"doc_map": 1400, "test_gen": 2600}
DOC_TOKENS_PER_SYMBOL = 180
DOC_TOKENS_BASE = 160

# Wait bounds (corrected plan: waits <= 15 min, only while the user is attending).
MAX_WAIT_S = 900
WORKER_HEARTBEAT_S = 5
WORKER_STALE_S = 30
WORKER_STARTUP_GRACE_S = 20

# GPU v2 MTP lanes. ctx is per slot, not the aggregate server context.
# Lane IDs remain stable; tier labels describe models, not measured quality.
DEFAULT_LANES: dict = {
    "lanes": {
        "lane-gpu-a": {
            "gpu": "example-24gb",
            "local": True,
            "tier": "a",
            "concurrency": 1,
            "ctx": 65536,
            "prompt_cap": 32768,
            "endpoint": {"base_url": "http://lane-gpu-a.agent-array-models.svc.cluster.local:8000", "api": "llamacpp",
                         "model": "local-code-a", "template": "chatml-nothink", "timeout_s": 300},
        },
        "lane-gpu-b": {
            "gpu": "example-24gb",
            "local": True,
            "tier": "b",
            "concurrency": 3,
            "ctx": 12288,
            "prompt_cap": 9432,
            "endpoint": {"base_url": "http://lane-gpu-b.agent-array-models.svc.cluster.local:8000", "api": "llamacpp",
                         "model": "local-code-b", "template": "chatml-nothink", "timeout_s": 300},
        },
    },
    # Model routing per card kind and difficulty: an ordered retry ladder (<= 3 rungs).
    # design S5 ladder, trimmed to 3 generations: easy starts on the volume lane, normal on lane-gpu-a (routing order is not a quality promotion).
    "routing": {
        "doc_map": {
            "easy": [
                {"lane": "lane-gpu-b", "temperature": 0.7, "steal_to": ["lane-gpu-a"]},
                {"lane": "lane-gpu-b", "temperature": 0.7, "feedback": True, "steal_to": ["lane-gpu-a"]},
                {"lane": "lane-gpu-a", "temperature": 0.9, "feedback": True},
            ],
            "normal": [
                {"lane": "lane-gpu-a", "temperature": 0.7, "steal_to": ["lane-gpu-b"]},
                {"lane": "lane-gpu-b", "temperature": 0.7, "feedback": True},
                {"lane": "lane-gpu-a", "temperature": 0.9, "feedback": True},
            ],
        },
        "test_gen": {
            "easy": [
                {"lane": "lane-gpu-b", "temperature": 0.7, "steal_to": ["lane-gpu-a"]},
                {"lane": "lane-gpu-b", "temperature": 0.7, "feedback": True},
                {"lane": "lane-gpu-a", "temperature": 0.9, "feedback": True},
            ],
            "normal": [
                {"lane": "lane-gpu-a", "temperature": 0.7},
                {"lane": "lane-gpu-b", "temperature": 0.7, "feedback": True},
                {"lane": "lane-gpu-a", "temperature": 0.9, "feedback": True},
            ],
        },
    },
    "steal": True,                  # try steal_to lanes when the rung's lane is saturated
    "gate_parallel": 3,             # concurrent gate runs (testers)
    "infra_retries": 3,             # endpoint errors retried per generation (they never count as a generation)
    "pause_bounce_fraction": 0.3,   # design S7: > 30% bounced -> the task pauses for re-decomposition
    "pause_min_done": 10,
    "max_inflight_units": 0,        # 0 = 2 x total lane slots + gate_parallel (Little's law, design Ãƒâ€šÃ‚Â§8)
}

DEFAULT_SAMPLING = {"top_p": 0.8, "top_k": 20, "repetition_penalty": 1.0,
                    "min_p": 0.0, "presence_penalty": 1.5}


def factory_home() -> Path:
    env = os.environ.get("FACTORY_HOME")
    if env:
        return Path(env)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "agent-array" / "factory"
    xdg = os.environ.get("XDG_STATE_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "state") / "agent-array" / "factory"


def enabled_flags(extra: list[str] | None = None) -> set[str]:
    flags = {f.strip() for f in os.environ.get("FACTORY_FLAGS", "").split(",") if f.strip()}
    if os.environ.get("FACTORY_ENABLE_TEST_GEN") == "1":
        flags.add(FLAG_TEST_GEN)
    for f in extra or []:
        flags.add(f)
    return flags


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "routing":
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_lanes(path: Path | None) -> dict:
    """Default lanes, overlaid with a lanes file. `lanes` entries merge per lane; `routing` replaces."""
    from .schema import validate_or_raise
    cfg = copy.deepcopy(DEFAULT_LANES)
    if path:
        over = json.loads(Path(path).read_text(encoding="utf-8"))
        validate_or_raise("lanes.v1", over, what=str(path))
        if over.get("replace_lanes"):
            cfg["lanes"] = {}
        cfg = _merge(cfg, over)
        if "routing" in over:
            routing = {} if over.get("replace_lanes") else copy.deepcopy(DEFAULT_LANES["routing"])
            for kind, by_diff in over["routing"].items():
                routing[kind] = {**routing.get(kind, {}), **by_diff}
            cfg["routing"] = routing
    check_lanes(cfg)
    return cfg


def check_lanes(cfg: dict) -> None:
    lanes = cfg.get("lanes") or {}
    if not lanes:
        raise ValueError("no lanes configured")
    for name, lane in lanes.items():
        if int(lane.get("concurrency", 1)) < 1:
            raise ValueError(f"lane {name}: concurrency must be >= 1")
        if int(lane.get("prompt_cap", 0)) + CTX_MARGIN >= int(lane.get("ctx", 0)):
            raise ValueError(f"lane {name}: prompt_cap + {CTX_MARGIN} must be below ctx")
    for kind, by_diff in (cfg.get("routing") or {}).items():
        for diff, ladder in by_diff.items():
            if not ladder or len(ladder) > MAX_GENERATIONS:
                raise ValueError(f"routing {kind}/{diff}: 1..{MAX_GENERATIONS} rungs required")
            for rung in ladder:
                for ln in [rung["lane"], *rung.get("steal_to", [])]:
                    if ln not in lanes:
                        raise ValueError(f"routing {kind}/{diff}: unknown lane {ln}")


def public_lanes(cfg: dict) -> dict:
    """Lanes config without key material (for logs, MANIFEST and status)."""
    out = copy.deepcopy(cfg)
    for lane in out.get("lanes", {}).values():
        ep = lane.get("endpoint", {})
        for k in ("key", "api_key"):
            ep.pop(k, None)
        if "key_file" in ep:
            ep["key_file"] = "(set)"
        if "key_env" in ep:
            ep["key_env"] = "(set)"
    return out

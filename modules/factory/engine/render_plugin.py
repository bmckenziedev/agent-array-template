"""Build the factory lane map from the same registry that creates lane Services."""
import json
import copy
from pathlib import Path
import runpy

PLUGIN_NAME = "factory-lanes-config"


def render(model, emit):
    keys = model["keys"]
    nodes = {node["name"]: node for node in model["org"]["nodes"]}
    lanes = {}
    settings = model["org"]["modules"].get("gpu-lanes", {})
    if settings.get("enabled"):
        for lane in sorted(settings.get("lanes", []), key=lambda lane: lane["name"]):
            name = lane["name"]
            lanes[name] = {"gpu": (nodes.get(lane["node"], {}).get("gpu") or {}).get("model", ""),
                "node": lane["node"], "tier": lane.get("tier", "a"), "local": True,
                "concurrency": lane["slots"], "ctx": lane["ctx"],
                "prompt_cap": lane.get("prompt_cap", lane["ctx"] // 2), "endpoint": {
                    "base_url": f"http://{name}.{keys['NS_MODELS']}.svc:8080/v1",
                    "api": "llamacpp", "model": lane["model_group"],
                    "template": lane.get("template", "chatml-nothink"), "timeout_s": 300,
                    "key_file": "/var/run/factory/model-auth/token"}}
    routing = {}
    if lanes:
        defaults = runpy.run_path(str(Path(__file__).parent / "factory_engine/config.py"))["DEFAULT_LANES"]
        routing = copy.deepcopy(defaults["routing"])
        names = sorted(lanes)
        aliases = {old: names[min(index, len(names) - 1)]
                   for index, old in enumerate(sorted(defaults["lanes"]))}
        for difficulties in routing.values():
            for ladder in difficulties.values():
                for rung in ladder:
                    rung["lane"] = aliases[rung["lane"]]
                    if "steal_to" in rung:
                        rung["steal_to"] = sorted({aliases[name] for name in rung["steal_to"]}
                                                  - {rung["lane"]})
    config = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {
        "name": "factory-config", "namespace": keys["NS_FACTORY"]},
        "data": {"lanes.json": json.dumps({"replace_lanes": True, "lanes": lanes, "routing": routing}, sort_keys=True)}}
    emit("global/modules/factory/engine/k8s/config.yaml", json.dumps(config, sort_keys=True, indent=2) + "\n")

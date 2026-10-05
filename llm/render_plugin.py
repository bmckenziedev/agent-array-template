"""Deterministic org account gateway rendering; no provider credentials are read."""
import json
import re
from pathlib import Path

PLUGIN_NAME = "llm"
KEY_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")
DEFAULT_MODELS = {
    "claude-api-standard": "anthropic/claude-sonnet-4-5-20250929",
    "openai-api-standard": "openai/gpt-4.1",
}


def subst(text, keys):
    def replace(match):
        if match[1] not in keys:
            raise KeyError(f"unknown placeholder {match[1]}")
        return str(keys[match[1]])
    return KEY_RE.sub(replace, text)


def dump(value):
    return json.dumps(value, sort_keys=True, indent=2) + "\n"


def provider_env(account):
    return "AA_PROVIDER_" + re.sub(r"[^A-Z0-9]", "_", account["id"].upper())


def render(model, emit):
    keys = model["keys"]
    def metadata(name):
        return {"name": name, "namespace": keys["NS_LLM"], "labels": {
            "app.kubernetes.io/name": name,
            "app.kubernetes.io/part-of": keys["PROJECT_NAME"],
            "app.kubernetes.io/component": "llm"}}
    settings = model["org"].get("components", {}).get("llm", {})
    models = {**DEFAULT_MODELS, **settings.get("models", {})}
    entries, env, secrets, types = [], [], [], {}
    accounts = sorted(model["accounts"]["accounts"], key=lambda a: a["id"])
    for account in accounts:
        if account["type"] != "api":
            continue
        secret = account["secret"]
        if secret["namespace"] not in ("llm", keys["NS_LLM"]):
            raise ValueError("API credentials must be in the llm namespace")
        env.append({"name": provider_env(account), "valueFrom": {
            "secretKeyRef": {"name": secret["name"], "key": secret["key"]}}})
        secrets.append({"namespace": keys["NS_LLM"], **{k: secret[k] for k in ("name", "key")}})
        for group in sorted(account["litellm_models"]):
            upstream = models.get(group)
            if not upstream or not upstream.startswith(account["vendor"] + "/"):
                raise ValueError(f"Configure components.llm.models.{group} for its vendor")
            if group in types:
                raise ValueError(f"Group {group} must belong to exactly one account")
            types[group] = "api"
            entries.append({"model_name": group, "litellm_params": {
                "model": upstream, "api_key": "os.environ/" + provider_env(account)},
                "model_info": {"id": account["id"] + "-" + group}})
    gpu = model["org"].get("modules", {}).get("gpu-lanes", {})
    if gpu.get("enabled"):
        for lane in sorted(gpu.get("lanes", []), key=lambda a: a["name"]):
            group = lane["model_group"]
            if not any(a["type"] == "pool" and group in a["litellm_models"] for a in accounts):
                raise ValueError(f"Lane group {group} has no pool account")
            types[group] = "pool"
            if any(e["model_name"] == group and "api_base" not in e["litellm_params"] for e in entries):
                raise ValueError("A model group cannot mix API and pool accounts")
            entries.append({"model_name": group, "litellm_params": {
                "model": "openai/" + group,
                "api_base": f"http://{lane['name']}.{keys['NS_MODELS']}.svc:8080/v1",
                "api_key": "os.environ/MODEL_SERVER_KEY",
                "max_parallel_requests": lane["slots"],
                "input_cost_per_token": 0, "output_cost_per_token": 0}})
        env.append({"name": "MODEL_SERVER_KEY", "valueFrom": {
            "secretKeyRef": {"name": "model-server-auth", "key": "api-key"}}})
    fallbacks = []
    configured = settings.get("fallbacks", {"local-coder": ["local-coder-small"]})
    for source, targets in sorted(configured.items()):
        if source not in types:
            continue
        valid = []
        for target in targets:
            if target not in types:
                continue
            if types[source] != types[target]:
                raise ValueError("Fallbacks cannot cross account types")
            valid.append(target)
        if valid:
            fallbacks.append({source: sorted(valid)})
    graph = {source: targets for edge in fallbacks for source, targets in edge.items()}
    visiting, visited = set(), set()
    def check_cycle(group):
        if group in visiting:
            raise ValueError("Fallback cycle")
        if group in visited:
            return
        visiting.add(group)
        for target in graph.get(group, []):
            check_cycle(target)
        visiting.remove(group)
        visited.add(group)
    for group in sorted(graph):
        check_cycle(group)
    config = {"model_list": entries,
              "litellm_settings": {"turn_off_message_logging": True,
                                   "callbacks": ["prometheus"],
                                   "prometheus_initialize_budget_metrics": True},
              "router_settings": {"routing_strategy": "simple-shuffle", "num_retries": 1,
                                  "enable_pre_call_checks": True, "fallbacks": fallbacks,
                                  "redis_host": "os.environ/REDIS_HOST",
                                  "redis_port": "os.environ/REDIS_PORT",
                                  "redis_password": "os.environ/REDIS_PASSWORD"},
              "general_settings": {"master_key": "os.environ/LITELLM_MASTER_KEY",
                                   "database_url": "os.environ/DATABASE_URL",
                                   "store_prompts_in_spend_logs": False,
                                   "enforce_fallback_model_access": True,
                                   "enforce_fallback_budget": True}}
    emit("global/llm/k8s/litellm-config.yaml", dump({"apiVersion": "v1", "kind": "ConfigMap",
         "metadata": metadata("litellm-config"),
         "data": {"config.yaml": dump(config)}}))
    deployment = json.loads(subst((Path(__file__).parent / "deployment.template.json").read_text(), keys))
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    container.setdefault("args", []).extend(["--prometheus_metrics_port", "4001"])
    container.setdefault("ports", []).append({"name": "metrics", "containerPort": 4001})
    if settings.get("enterprise_license", False):
        env.append({"name": "LITELLM_LICENSE", "valueFrom": {"secretKeyRef": {
            "name": "litellm-env", "key": "LITELLM_LICENSE", "optional": True}}})
    container["env"].extend(env)
    deployment["spec"]["template"]["spec"]["volumes"].append({
        "name": "admin", "configMap": {"name": "litellm-admin"}})
    deployment["spec"]["template"]["spec"]["containers"][0]["volumeMounts"].append({
        "name": "admin", "mountPath": "/opt/llm-admin", "readOnly": True})
    emit("global/llm/k8s/deployment.yaml", dump(deployment))
    teams = []
    for team in sorted(model["teams"], key=lambda t: t["id"]):
        policy = team["litellm"]
        teams.append({"team_id": team["id"], "models": sorted(policy["models"]),
                      "max_budget": policy["max_budget_usd"],
                      "budget_duration": policy["budget_duration"],
                      "tpm_limit": policy["tpm_limit"], "rpm_limit": policy["rpm_limit"],
                      "members_with_roles": [{"user_id": u["slug"],
                          "role": "admin" if settings.get("enterprise_license", False) and u["slug"] in team["leads"] else "user"}
                          for u in sorted(model["users"], key=lambda u: u["slug"])
                          if team["id"] in u["teams"] and u["status"] == "active"],
                      "metadata": {"managed_by": "llm", "task_budget_usd": policy["task_budget_usd"]}})
    users = [{"user_id": u["slug"], "user_email": u["email"], "teams": sorted(u["teams"]),
              "user_role": "internal_user",
              "metadata": {"managed_by": "llm", "oidc_sub": u["oidc_sub"]}}
             for u in sorted(model["users"], key=lambda u: u["slug"])
             if u["status"] == "active"]
    directory = dump({"teams": teams, "users": users, "provider_secrets": secrets})
    emit("files/llm/teams.json", directory)
    emit("global/llm/k8s/litellm-admin.yaml", dump({"apiVersion": "v1", "kind": "ConfigMap",
         "metadata": metadata("litellm-admin"),
         "data": {"teams.json": directory,
                  "teams_sync.py": (Path(__file__).parent / "teams_sync.py").read_text()}}))
    emit("files/llm/provider-env.json", dump(env))

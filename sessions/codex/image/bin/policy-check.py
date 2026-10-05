#!/usr/bin/env python3
import sys, tomllib
from pathlib import Path
d = Path(sys.argv[1])
req = tomllib.loads((d / "requirements.toml").read_text(encoding="utf-8"))
mc = tomllib.loads((d / "managed_config.toml").read_text(encoding="utf-8"))
bad = []
if mc.get("forced_login_method") != "chatgpt":
    bad.append("forced_login_method must be chatgpt")
if not isinstance(mc.get("forced_chatgpt_workspace_id"), str) or not mc["forced_chatgpt_workspace_id"].strip():
    bad.append("forced_chatgpt_workspace_id must identify the organisation workspace")
def want(cond, msg):
    if not cond:
        bad.append(msg)
want(isinstance(req.get("allowed_approval_policies"), list) and set(req["allowed_approval_policies"]) <= {"never", "on-request", "untrusted"} and bool(req["allowed_approval_policies"]),
     'requirements: allowed_approval_policies must contain supported team modes')
want(req.get("allowed_approvals_reviewers") == ["user"],
     'requirements: allowed_approvals_reviewers must be ["user"] (no automatic approval)')
modes = req.get("allowed_sandbox_modes")
want(isinstance(modes, list) and "read-only" in modes
     and set(modes) <= {"read-only", "workspace-write", "danger-full-access"},
     "requirements: allowed_sandbox_modes must contain read-only, and "
     "nothing but read-only/workspace-write/danger-full-access")
want(req.get("allowed_web_search_modes") == [], "requirements: allowed_web_search_modes must be [] (search disabled)")
want(req.get("allowed_login_methods") == ["chatgpt"], 'requirements: allowed_login_methods must be ["chatgpt"]')
want(req.get("cli_auth_credentials_store") == "file", 'requirements: cli_auth_credentials_store must be "file"')
want(req.get("allow_remote_control") is False, "requirements: allow_remote_control must be false")
want(req.get("allow_managed_hooks_only") is True, "requirements: allow_managed_hooks_only must be true")
want(req.get("allow_browser_and_computer_use") is False, "requirements: allow_browser_and_computer_use must be false")
want(isinstance(req.get("mcp_servers"), dict), "requirements: mcp_servers must be an explicit managed map")
feat = req.get("features") or {}
for f in ("daemon_auto_start", "apps", "in_app_local_automation", "goals", "memories",
          "guardian_approval", "plugins", "remote_plugin", "in_app_updates", "computer_use", "browser_use"):
    want(feat.get(f) is False, f"requirements: [features] {f} must be false")
rules = (req.get("rules") or {}).get("prefix_rules") or []
tokens = set()
for r in rules:
    if r.get("decision") == "forbidden":
        for p in (r.get("pattern") or [])[:1]:
            tokens.update(p.get("any_of") or [p.get("token")])
for t in ("curl", "wget", "ssh", "nc", "socat", "codex", "tmux", "node", "npm"):
    want(t in tokens, f"requirements: [rules] must forbid `{t}`")
want(mc.get("approval_policy") in req.get("allowed_approval_policies", []), 'managed_config: approval_policy must match team requirements')
want(mc.get("sandbox_mode", "workspace-write") in modes, "managed sandbox must be in allowed modes")
want(mc.get("web_search") == "disabled", 'managed_config: web_search must be "disabled"')
want(mc.get("check_for_update_on_startup") is False, "managed_config: check_for_update_on_startup must be false")
want((mc.get("sandbox_workspace_write") or {}).get("network_access") is False,
     "managed_config: [sandbox_workspace_write] network_access must be false")
want((mc.get("history") or {}).get("persistence") == "none", 'managed_config: [history] persistence must be "none"')
want((mc.get("feedback") or {}).get("enabled") is False, "managed_config: [feedback] enabled must be false")
want((mc.get("analytics") or {}).get("enabled") is False, "managed_config: [analytics] enabled must be false")
for m in bad:
    print("policy: " + m, file=sys.stderr)
sys.exit(1 if bad else 0)

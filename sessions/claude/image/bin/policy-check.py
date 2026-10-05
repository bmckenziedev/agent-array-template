#!/usr/bin/env python3
import json, sys
p = json.load(open(sys.argv[1], encoding="utf-8"))
perm = p.get("permissions") or {}
deny = set(perm.get("deny") or [])
problems = []
if perm.get("defaultMode") not in ("default", "acceptEdits", "plan", "bypassPermissions"):
    problems.append("permissions.defaultMode must be a supported team permission mode")
required_deny = (
    "WebFetch", "WebSearch",
    "Read(~/.claude/.credentials.json)", "Edit(~/.claude/.credentials.json)",
    "Read(~/.claude/.device-keys.json)", "Edit(~/.claude/.device-keys.json)",
    "Edit(~/.claude/.claude.json)", "Edit(~/.claude.json)", "Edit(~/.claude/settings.json)",
    "Edit(~/.claude/settings.local.json)", "Edit(//work/.claude/**)", "Edit(//work/.mcp.json)",
    "Edit(//work/*/.claude/**)", "Edit(//work/*/.mcp.json)",  # each pod's listener dir /work/aa-<node>-<n>
    "Edit(//work/snap/**)",
    "Bash(npm *)", "Bash(npx *)", "Bash(node *)", "Bash(pnpm *)", "Bash(pnpx *)", "Bash(pn *)",
    "Bash(pnx *)", "Bash(yarn *)", "Bash(corepack *)", "Bash(bun *)", "Bash(deno *)", "Bash(jest *)",
    "Bash(curl *)", "Bash(wget *)", "Bash(ssh *)", "Bash(scp *)", "Bash(sftp *)", "Bash(nc *)",
    "Bash(socat *)",
    "Bash(git push *)", "Bash(git fetch *)", "Bash(git pull *)", "Bash(git clone *)",
    "Bash(git remote *)", "Bash(git submodule *)", "Bash(git config *)",
    "Bash(rg *--pre*)", "Bash(rg *--hostname-bin*)", "Bash(fd *--exec*)", "Bash(fd *-x*)",
    "Bash(fd *-X*)", "Bash(git *--open-files-in-pager*)", "Bash(git * -O*)", "Bash(git *--output*)",
    "Bash(aa-snapshot receive *)", "Bash(aa-snapshot extend *)",
    "Bash(aa-snapshot down *)", "Bash(aa-snapshot ack *)",
)
for rule in required_deny:
    if rule not in deny:
        problems.append(f"permissions.deny must contain {rule}")
runners = ("rg", "fd", "fdfind", "find", "xargs", "env", "git grep", "sh", "bash", "dash",
           "python", "python3", "perl", "node", "npx", "tmux", "timeout", "nice", "nohup")
for rule in perm.get("allow") or []:
    r = " ".join(str(rule).split())
    if r in ("Bash", "Bash(*)") or any(r == f"Bash({c})" or r.startswith(f"Bash({c} ")
                                       or r.startswith(f"Bash({c}:") for c in runners):
        problems.append(f"permissions.allow must not pre-approve {rule}: it can run other programs")
if not isinstance(p.get("allowedMcpServers"), list):
    problems.append("allowedMcpServers must be a list (an empty list blocks every MCP server)")
if p.get("allowManagedMcpServersOnly") is not True:
    problems.append("allowManagedMcpServersOnly must be true")
if p.get("disableClaudeAiConnectors") is not True:
    problems.append("disableClaudeAiConnectors must be true")
if not isinstance(p.get("forceLoginOrgUUID"), str) or not p["forceLoginOrgUUID"].strip():
    problems.append("forceLoginOrgUUID must identify the organisation seat")
if p.get("forceLoginMethod") != "claudeai":
    problems.append('forceLoginMethod must be "claudeai"')
env = p.get("env") or {}
risky = {"NODE_OPTIONS", "NODE_EXTRA_CA_CERTS", "NODE_TLS_REJECT_UNAUTHORIZED", "SSL_CERT_FILE",
         "SSL_CERT_DIR", "BUN_OPTIONS", "BUN_CONFIG_FILE", "LD_PRELOAD", "LD_LIBRARY_PATH",
         "LD_AUDIT", "BASH_ENV", "ENV", "DISABLE_GROWTHBOOK", "DISABLE_TELEMETRY", "DO_NOT_TRACK",
         "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"}
for k in env:
    if (k in risky or k.startswith("ANTHROPIC_")
            or (k.startswith("CLAUDE") and not k.startswith("CLAUDE_CODE_DISABLE_"))):
        problems.append(f"env must not set {k}")
if p.get("disableRemoteControl") is True:
    problems.append("disableRemoteControl is true; the pod exists to serve Remote Control")
for msg in problems:
    print("policy: " + msg, file=sys.stderr)
sys.exit(1 if problems else 0)

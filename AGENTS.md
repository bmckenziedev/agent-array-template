# Coding-agent adaptation guide

This repository is a self-hosted multi-user coding-agent platform template for Kubernetes.
Interactive vendor seats run in private per-user sandboxed sessions.
Headless automation uses organisation API accounts through a model gateway.
Registries define people, teams, accounts, tools and classified context.
Reviewed rendered manifests drive GitOps; optional modules extend the platform.

## Side-by-side adoption (read first)

Existing services must never be affected ([adoption](docs/ADOPTION.md#side-by-side-adoption)).
Deploy side by side in a new cluster or new isolated namespaces. Share no identities,
secrets, service accounts, DNS names, ingress, storage or data with any existing system.
Make no changes to existing clusters, CI, IdP groups, firewalls or DNS. Use existing
systems read-only, and only when an adoption step requires it. **Stop and report** any
step that would touch an existing service. Pilot two users first; never import old logins.

## First hour

Local only: Git, Python 3.10+, Make and Bash (Git Bash/WSL on Windows). Nothing here
deploys or contacts a cluster. Run every command from the repository root.

1. **Clone** your organisation's private copy of the template and prepare tooling:
   ```sh
   git clone <private-copy-of-template> agent-array && cd agent-array
   python -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt
   ```
   PyYAML (from `requirements-dev.txt`) is needed by `check_contracts.py`,
   `validate_manifests.sh` and `module_config.py`; the renderer itself is stdlib-only.
2. **Render the example org** (Example Org: payments/platform teams, users Ana and Bo).
   Zero image digests produce warnings; that is expected without `--strict`:
   ```sh
   python tools/render/render.py --org org/org.example.yaml --out rendered
   python tools/render/render.py --org org/org.example.yaml --out rendered --check
   ```
   Inspect `rendered/users/ana/` (namespace `aa-u-ana`), `rendered/global/`,
   `rendered/files/SECRETS-REQUIRED.md` and the org-directory ConfigMaps under
   `rendered/global/tools/render/k8s/`.
3. **Run the offline gates.** The `make render|check|validate` targets read `org/org.yaml`,
   which a fresh clone does not have; until you create it, validate the example render directly:
   ```sh
   make test lint sanitize docs
   python tools/ci/check_contracts.py --rendered rendered
   bash tools/ci/validate_manifests.sh rendered   # needs kubeconform or kubectl
   python tools/ci/lint_cel.py rendered
   ```
   Exercise the all-module and strict paths with the synthetic fixture (never deploy it):
   ```sh
   tmp=$(mktemp -d)
   python tools/ci/module_config.py --strict-fixture --out "$tmp/org"
   python tools/render/render.py --org "$tmp/org/org.example.yaml" --out "$tmp/rendered" --strict
   rm -rf "$tmp"
   ```
   `tools/ci/install_tools.sh` installs checksum-verified kubeconform/promtool. A missing
   tool is an explicit SKIP, never passing evidence.
4. **Read the open issues.** VERIFY gates are tracked as template issues
   (`gh issue list --label kind/verify`) and summarised with issue numbers in
   [integration status](docs/INTEGRATION.md#status-at-handover). List in-tree markers
   with `grep -rn VERIFY --include=*.md .`. Each open gate blocks its capability.
5. **Read the decisions:** [ADRs](docs/adr/README.md), [architecture](docs/ARCHITECTURE.md),
   [security](docs/SECURITY.md), [multi-user](docs/MULTI-USER.md), then the
   [bootstrap order](docs/ADOPTION.md#bootstrap-order-and-verification-gates).

## Where things live

| Path | Authored or generated | Kind | Scope |
|---|---|---|---|
| `org/*.example.yaml`, `org/schema/` | Authored | Config: registries and JSON Schema | Global input |
| `org/*.yaml`, `mcp/*.yaml` (your copies) | Authored by adopter | Config: deployment values | Global input |
| `mcp/*.example.yaml` | Authored | Config: MCP registry, context sources | Per MCP server |
| `<component>/org.component.defaults.yaml` | Authored | Config: `C_`/`M_` defaults | Per component/module |
| `<component>/k8s/*.tmpl.yaml`, `helm/` | Authored | Manifest templates, Helm values | Global unless `.per-<scope>.` |
| `*.per-user.tmpl.*`, `*.per-user-tool.tmpl.*` | Authored | Templates (mostly `sessions/`) | Per user / user-tool |
| `*.per-team.tmpl.*`, `*.per-node.tmpl.*`, `*.per-mcp.tmpl.*` | Authored | Templates | Per team / node / MCP |
| `<component>/render_plugin.py` | Authored | Code: stdlib-only render plugin | Directory-scoped |
| `services/`, `portal/panel/app/`, `mcp/servers/`, `tools/` | Authored | Code with `tests/` | Global |
| `sessions/*/image/`, `services/*/Dockerfile` | Authored | Code: pinned images | Global |
| `modules/<name>/` | Authored | Code, templates, defaults | Global; off by default |
| `docs/`, `docs/adr/`, `docs/runbooks/` | Authored | Decisions and procedures | n/a |
| `.github/workflows/` | Authored | CI config (offline only) | n/a |
| `rendered/global/`, `rendered/mcp/` | Generated | Manifests synced by Argo CD | Global / per MCP |
| `rendered/users/<slug>/` | Generated | Manifests synced by Argo CD | Per user (and per tool) |
| `rendered/files/` | Generated | Scripts, Helm values, `SECRETS-REQUIRED.*` | Separate consumers |
| `rendered/global/tools/render/k8s/org-directory-*.yaml` | Generated | Read-only identity/account directory | Global, per namespace |

`rendered/` is gitignored in the template; an adopting repository commits it with its org
configuration ([org reference](org/README.md)). Regenerate it; never hand-edit generated files.

## Adaptation checklist

1. Copy `org/*.example.yaml` to `org/*.yaml` plus the `mcp/*.example.yaml` files, fill
   deployment values and identities, then point org.yaml `files` at the copies.
2. Set `project.name`, label/image prefixes and controlled endpoints. Replace synthetic
   identities deliberately in private adoption configuration.
3. Choose approved vendors/modules. Start modules disabled; preserve one-person seats and
   API-only automation. Legal/data approval precedes vendor enablement.
4. Run `make render`; inspect per-user resources, policy, account routes and
   `rendered/files/SECRETS-REQUIRED.md`. Replace placeholder digests and `home_node: auto`
   with verified values before strict render.
5. Run the gates in [Every test suite](#every-test-suite), including strict rendering,
   `make check` (deterministic rerender) and both module gate states.
6. Follow the [bootstrap order](docs/ADOPTION.md#bootstrap-order-and-verification-gates);
   a failed gate stops dependent steps. Decisions live in ADRs; deployment values live in
   registries/defaults.

## Invariants that must never weaken

| Invariant | Enforced by | Reason |
|---|---|---|
| R1: official unmodified pinned CLI, vendor flow and org-workspace lock | `sessions/claude/image/Dockerfile`, `sessions/claude/image/bin/policy-check.py`, `sessions/claude/k8s/policy.per-user-tool.tmpl.yaml` | Prevent credential extraction, proxying and consumer-account substitution |
| R2: private user/tool/node login, never copied/moved/backed up | `sessions/k8s/aa-session-login-pv.tmpl.yaml`, `ops/backup/backup.py`, `ops/backup/velero/check_resource_policy.py` | Protect person boundaries and refresh-token rotation |
| R3: holder starts each seat turn | `services/supervisor/aa_supervisor/policy.py`, `services/pace/pace.py` (`holder_required`) | Prevent queue/cron/webhook use of interactive allowance |
| R4: per-account concurrency/cap/start spacing | `services/pace/pace.py`, `services/pace/tests/test_service.py` | More pods do not create vendor allowance |
| R5: holder-only session access, ticketed audited break-glass | `sessions/k8s/aa-session-exec.tmpl.yaml`, `sessions/common/bin/aa-admission-webhook` | Preserve private homes and actor attribution |
| Interactive seats, API automation, no Claude Code routing to non-Claude models | `llm/render_plugin.py`, `sessions/common/bin/entrypoint-check` | Preserve vendor-contract and credential boundaries |
| Optional CEL reads use `has()`; positive/negative server dry-runs before Deny binding | `tools/ci/lint_cel.py`, `platform/hardening/tests/live/check_admission.py`, [ADR 0008](docs/adr/0008-admission-has-guards-and-dry-runs.md) | Avoid missing-field outages and validation bypasses |
| Default-deny plus API endpoint AND service-IP egress | `sessions/k8s/user/netpol.per-user.tmpl.yaml`, `portal/api-egress/render_plugin.py`, `platform/hardening/tests/test_hardening.py` | CNI DNAT order can otherwise break control/identity services |
| Never Helm `--reuse-values`; complete explicit `-f` files | `argocd/install.sh` and peer `install.sh`; rule in [upgrade runbook](docs/runbooks/upgrade.md) (no automated check) | Avoid stale policy/credential settings |
| Never detached processes | `modules/session-jobs/tester.py`, `portal/e2e/run_e2e.py`; review-enforced elsewhere | Bound lifecycle and guarantee test cleanup |
| Firewall changes additive and explicitly gated | `modules/hostguard/hostguard.py`, `modules/k3s-baremetal/40-firewall.sh` | Preserve administration/recovery connectivity |
| Secrets by name/key only; no upstream credentials in sessions | `tools/render/aa_render/secrets_index.py`, `org/schema/accounts.schema.json`, `tools/ci/check_contracts.py` | Keep plaintext/broad authority out of tenant and control diffs |
| Digest pins, managed hashes, fail-closed entrypoints | `tools/render/aa_render/cli.py` (`--strict`), `sessions/common/bin/aa-policy-merge`, `sessions/claude/image/bin/entrypoint.sh` | Preserve reviewed artifact/policy identity |
| Sanitization gate passes without weakened rules | `tools/sanitize/scan.py`, `tools/sanitize/rules.json` | Prevent personal data/private topology/credential leakage |

Proxy identity, labels, discovered processes and caller-supplied teams do not confer
authority. Check rendered identity/entitlement at the service boundary. Node-root and
cluster-root remain trusted; do not claim namespace isolation removes this risk.

## Extension points

- **Module:** add `modules/<name>/` with uniform README, defaults (`scope: modules`),
  templates, `secrets.required.yaml` and offline tests. Extend schema/reference for its
  enable key; disabled modules emit nothing. Extra namespaces retain PSA/default-deny.
  Update the [module index](modules/README.md) and dependencies.
- **MCP server:** register clients/transport/teams; bake stdio into pinned images or deploy
  HTTP with TokenReview, narrow RBAC, audit and exact egress. Keep upstream credentials
  in the server. Follow the [server runbook](docs/runbooks/add-mcp-server.md).
- **Context source:** declare classification, delivery and estate linkage; implement
  connector/provenance/retention and receiver checks. Declaring a feed does not implement
  it. Follow the [context policy](docs/MCP-AND-CONTEXT.md).
- **Vendor:** extend schema/normalisation, accounts, client/gateway policy, egress and tests
  together. Require ADR and contractual/data approval; never convert seats to pooled
  automation or bypass official credential controls.
- **Notifier:** add approved adapter and named Secret recipe, bounded retries, audit and
  metrics; test synthetic delivery/heartbeat. Preserve unknown-severity fallback.

## Conventions

Placeholders are exact uppercase `{{KEY}}`, unknown keys fail. Quote strings in YAML;
leave `_JSON` unquoted. `{{LBRACE2}}` escapes literal braces. Settings belong in component
defaults as `C_`/`M_` keys, not invented global tokens. Render scopes: global, user,
user-tool, team, node, mcp, account (`<name>.per-<scope>.tmpl.<ext>`). `RENDER-IF` only
narrows activation; module enable keys gate automatically. Plugins are stdlib-only,
deterministic, no-network and directory-scoped. Argo trees reject `kustomization.yaml`,
`Chart.yaml` and values files; Helm inputs belong in `files/`. See [renderer](tools/render/README.md).

`k8s/` contains manifests only; Helm directories contain values only. Hardening declares
core namespaces. READMEs use Interface, Configuration, Secrets, Deploy, Verify, Rollback,
Security notes, preceded by purpose; module READMEs start `# Module: <name> (optional)`.
Use UTF-8, LF, trailing newline and the [strict YAML subset](org/README.md).

Audit shape is `{ts, component, event, actor: {user, sa, sub}, team, target, outcome,
detail}` with UTC RFC3339 and `allow|deny|error`, one JSON line on stdout. Never log
prompts, tool payloads, tokens or file contents. Metrics use `aa_`; services normally
listen on 8080 (port name `http`) with `/metrics`. Exceptions: LiteLLM metrics on 4001,
and the supervisor pushes metrics via its connector ([contracts](docs/CONTRACTS.md)).
Keep task IDs out of labels. Services use read-only org-directory for identity/account resolution.

## Every test suite

`make` targets: `render`, `check`, `validate` (use `org/org.yaml`), `lint`, `test`,
`sanitize`, `docs`, `all`. With your org configuration in place:

```sh
make render
make check
make test
make lint
make validate
make sanitize
make docs
python tools/render/render.py --org org/org.yaml --out rendered --strict
python tools/ci/lint_cel.py rendered
bash tools/ci/promtool_tests.sh rendered org/org.yaml
```

`make test` runs `tools/ci/run_tests.py`, `make docs` runs `tools/ci/linkcheck.py --root .`
and `make sanitize` runs `tools/sanitize/scan.py --root .`. For the all-module state, use
`tools/ci/module_config.py` as in [First hour](#first-hour) and repeat render, contract,
CEL and promtool checks on that tree, as [CI](.github/workflows/ci.yml) does.

Unit suites: `python -m unittest discover -s <component>/tests -t <component>`.
Shell suites: `bash <component>/tests/test-*.sh` using Git Bash/WSL. Third-party/pytest
suites use component pinned requirements and documented commands. Mock e2e, template,
kubeconform and promtool suites follow [CI](tools/ci/README.md) and component Verify
sections. Exercise scopes, denial cases, all-module rendering and both module gate states.
Missing tools are explicit skips, not passing evidence. Test servers run in-process on
loopback port 0 and stop before returning.

Live suites use `check_*.py` or `tests/live/`, take `--kubeconfig` and default to server
dry-run. They are excluded from offline CI and run only by authorised platform admins.
Baseline admission tests include plain Pod/Deployment fixtures and negatives; session
exact shapes require additional tests. Never run live mutations from CI.

## VERIFY markers

VERIFY is unproven behavior of a pinned CLI/chart/API/cluster. Block the affected
capability until positive and relevant negative evidence exists. Record reproducible
non-sensitive artifact/version evidence in the component Verify section, update the
marker and ADR if behavior differs. Gates include workspace locks, managed MCP paths,
token refresh, CONNECT support, gateway edition features and CNI egress. Never clear
a marker from an assumption or Ready pod.

## Do not

- Affect existing services; see [Side-by-side adoption](#side-by-side-adoption-read-first).
- Add personal identifiers, private topology, live reports or source history.
- Put secrets/login files/real ciphertext fixtures in org.yaml or examples.
- Make live changes from CI or leave detached processes.
- Drive another person's seat, copy logins or give untrusted testers task credentials.
- Weaken admission, egress, classification or sanitization to make tests pass.

Additional extension contracts: record session kind and handle before enabling controls;
authorise each command, bind work items by CLI session ID, and preserve console-independent
holder access. Permission adapters follow the category/tier floor, deny on timeout and
notify once. Redact every relay path. Notifier/work-item adapters need bounded retries,
audit and a dedicated rotating connector credential; console comparison checks every
configured credential in constant time. Forge credentials are per-user named Secrets,
provisioned/revoked with the registry identity. Check account/user refusal lists before
leases; stale readings and near-cap advisory never authorise seat automation.

## Session supervision

Policy lives in `services/supervisor/org.component.defaults.yaml`, `team_policies` and the
plugin-produced supervisor-policy ConfigMap (`services/supervisor/render_plugin.py`).
Notifiers and work-item adapters live in `services/supervisor/aa_supervisor/notifiers` and
`aa_supervisor/adapters`; the policy hook uses the same redacted projected-token relay
path. The connector is `aa_supervisor/connector.py`, with `services/supervisor/PROTOCOL.md`
and `services/supervisor/console_ref/verify.py` defining receiver trust. See
[supervision](docs/SUPERVISION.md).

Never weaken holder-only spawn, separate PID namespaces, transcript-only mounts, same-UID/drop-ALL hardening, private control mounts, request-only permission sockets, no Secret/listeners/PID signals, O_NOFOLLOW, redaction, per-command authority, input limits or the stop governor. Confirm pinned Claude stream-json and permission contracts, Codex stdin/JSON/input/id, tmux argv/literal keys/socket/version, MCP env inheritance and tini subreaper before clearing reviewed VERIFY gates. VERIFY 08 VM FIFO/token-rotation checks and gVisor remain deploy-phase evidence.

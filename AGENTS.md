# Coding-agent adaptation guide

This repository is a self-hosted multi-user coding-agent platform template for Kubernetes.
Interactive vendor seats run in private per-user sandboxed sessions.
Headless automation uses organisation API accounts through a model gateway.
Registries define people, teams, accounts, tools and classified context.
Reviewed rendered manifests drive GitOps; optional modules extend the platform.

## Adaptation checklist

1. Copy `org/*.example.yaml` to `org/*.yaml` plus MCP/context examples, fill deployment
   values and identities, then point org.yaml `files` at the copies.
2. Set `project.name`, label/image prefixes and controlled endpoints. Replace synthetic
   identities deliberately in private adoption configuration.
3. Choose approved vendors/modules. Start modules disabled; preserve one-person seats and
   API-only automation. Legal/data approval precedes vendor enablement.
4. Run `make render`; inspect per-user resources, policy, account routes and generated
   required Secrets. Replace placeholder digests with verified artifacts before strict render.
5. Run local CI gates: `make test`, `make validate`, `make sanitize`, `make docs`, strict
   rendering and deterministic rerendering, including enabled/disabled module coverage.
6. Review [ADRs](docs/adr/README.md), [adoption](docs/ADOPTION.md) and component READMEs.
   Decisions live in ADRs; deployment values live in registries/defaults. Templates, source
   and plugins are authored; `rendered/` and org-directory are generated. Regenerate outputs.

## Invariants that must never weaken

| Invariant | Reason |
|---|---|
| R1: official unmodified pinned CLI, vendor flow and org-workspace lock | Prevent credential extraction, proxying and consumer-account substitution |
| R2: private user/tool/node login, never copied/moved/backed up | Protect person boundaries and refresh-token rotation |
| R3: holder starts each seat turn | Prevent queue/cron/webhook use of interactive allowance |
| R4: per-account concurrency/cap/start spacing | More pods do not create vendor allowance |
| R5: holder-only session access, ticketed audited break-glass exception | Preserve private homes and actor attribution |
| Interactive seats, API automation, no Claude Code routing to non-Claude models | Preserve vendor-contract and credential boundaries |
| Optional CEL reads use `has()`; positive/negative server dry-runs before Deny binding | Avoid missing-field outages and validation bypasses |
| Default-deny plus API endpoint AND service-IP egress where needed | CNI DNAT order can otherwise break control/identity services |
| Never Helm `--reuse-values`; complete explicit `-f` files | Avoid stale policy/credential settings |
| Never detached processes | Bound lifecycle and guarantee test cleanup |
| Firewall changes additive and explicitly gated | Preserve administration/recovery connectivity |
| Secrets by name/key only; no upstream credentials in sessions | Keep plaintext/broad authority out of tenant and control diffs |
| Digest pins, managed hashes, fail-closed entrypoints | Preserve reviewed artifact/policy identity |
| Sanitization gate passes without weakened rules | Prevent personal data/private topology/credential leakage |

Proxy identity, labels, discovered processes and caller-supplied teams do not confer
authority. Check rendered identity/entitlement at the service boundary. Node-root and
cluster-root remain trusted; do not claim namespace isolation removes this risk.

## Extension points

- **Module:** add `modules/<name>/` with uniform README, defaults (`scope: modules`),
  templates, required Secrets and offline tests. Extend schema/reference for its enable
  key; disabled modules emit nothing. Extra namespaces retain PSA/default-deny. Update
  [module index](modules/README.md) and dependencies.
- **MCP server:** register clients/transport/teams; bake stdio into pinned images or deploy
  HTTP with TokenReview, narrow RBAC, audit and exact egress. Keep upstream credentials
  in the server. Follow [server runbook](docs/runbooks/add-mcp-server.md).
- **Context source:** declare classification, delivery and estate linkage; implement
  connector/provenance/retention and receiver checks. Declaring a feed does not implement
  it. Follow [context policy](docs/MCP-AND-CONTEXT.md).
- **Vendor:** extend schema/normalisation, accounts, client/gateway policy, egress and tests
  together. Require ADR and contractual/data approval; never convert seats to pooled
  automation or bypass official credential controls.
- **Notifier:** add approved adapter and named Secret recipe, bounded retries, audit and
  metrics; test synthetic delivery/heartbeat. Preserve unknown-severity fallback.

## Conventions

Placeholders are exact uppercase `{{KEY}}`, unknown keys fail. Quote strings in YAML;
leave `_JSON` unquoted. `{{LBRACE2}}` escapes literal braces. Settings belong in component
defaults as `C_`/`M_` keys, not invented global tokens. Render scopes: global, user,
user-tool, team, node, MCP, account. `RENDER-IF` only narrows activation; module enable
keys gate automatically. Plugins are stdlib-only, deterministic, no-network and
directory-scoped. See [renderer](tools/render/README.md).

`k8s/` contains manifests only; Helm directories contain values only. Hardening declares
core namespaces. READMEs use Interface, Configuration, Secrets, Deploy, Verify, Rollback,
Security notes, preceded by purpose; modules start `Module: <name> (optional)`.
Use UTF-8, LF, trailing newline and the [strict YAML subset](org/README.md).

Audit shape is `{ts, component, event, actor: {user, sa, sub}, team, target, outcome,
detail}` with UTC RFC3339 and `allow|deny|error`, one JSON line on stdout. Never log
prompts, tool payloads, tokens or file contents. Metrics use `aa_`; services normally
listen on 8080 with `/metrics`. Keep task IDs out of labels. Services use read-only
org-directory for identity/account resolution.

## Every test suite

```sh
make render
make test
make validate
make sanitize
make docs
python tools/render/render.py --org org/org.yaml --out rendered --strict
python tools/ci/run_tests.py
python tools/ci/lint_cel.py rendered
python tools/ci/linkcheck.py --root .
python tools/sanitize/scan.py --root .
```

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

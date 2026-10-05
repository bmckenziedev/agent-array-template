# Contributing

Preserve identity, account and data boundaries. Read [agent adaptation](AGENTS.md),
[architecture](docs/ARCHITECTURE.md) and [security](docs/SECURITY.md) before changing shared
contracts. Component maintainers review directory changes; platform/security maintainers
review cluster-wide policy, identity, secrets and GitOps.

## Layout and directory responsibility

| Directory | Responsibility |
|---|---|
| `org/` | Authored schema, examples and registries; no secret values |
| `tools/render/` | Normalisation, substitution, scope/gates and generated directory contract |
| `cluster/`, `argocd/`, `platform/` | Cluster identity/storage, GitOps and common hardening |
| `sessions/` | Per-user policies, images, homes, workloads and exec guard |
| `llm/`, `services/pace/`, `services/farm-mcp/` | API models/budgets, leases and authenticated dispatch |
| `mcp/`, `portal/` | Tool/context integration and console/task authority |
| `monitoring/`, `ops/` | Telemetry, audit, backup/recovery |
| `modules/` | Independently enabled optional components |
| `docs/` | Current-state documentation, runbooks and ADRs |
| `rendered/` | Generated reviewable GitOps outputs, never hand-edited |

## Authoring conventions

Use UTF-8, LF and a trailing newline. YAML excludes tabs, duplicate keys, anchors/tags,
multiline scalars, floats and nested flow collections. Quote ambiguous values. Follow
[org schema](org/README.md) and [renderer](tools/render/README.md).

Placeholders are exact `{{KEY}}` uppercase/digit/underscore tokens without spaces.
Unknown keys fail. Quote YAML string replacements and leave `_JSON` unquoted; use
`{{LBRACE2}}` for a literal opening delimiter. Global templates use `.tmpl.<ext>`;
entities use `.per-<scope>.tmpl.<ext>` for user, user-tool, team, node, MCP and account.
Claude/Codex/Kimi path segments filter user-tool templates. `RENDER-IF` narrows subtrees.

New settings live in `org.component.defaults.yaml` under components/modules and become
`C_<COMPONENT>_<KEY>` or `M_<MODULE>_<KEY>`. Do not invent global keys.
`secrets.required.yaml` declares name, namespace reference, keys, purpose and recipe,
never values. Plugins define unique `PLUGIN_NAME` and `render(model, emit)`, are
stdlib-only, sorted/deterministic, no-network, and read/emit only permitted directory scopes.

`k8s/` contains manifests only, scripts/docs beside it; Helm directories contain values
only. Argo reads generated manifests. Hardening declares core namespaces; sessions declare
user namespaces; modules declare only additional namespaces with the same baseline.

Guard every optional CEL read with `has()`. Namespace-label bindings require positive
plain Pod/Deployment and negative server dry-runs before Deny/Fail. Helm uses complete
explicit `-f` files, never `--reuse-values`. Host helpers default to dry-run with explicit
mutation flags; firewall changes are additive. Never detach processes; test servers run
in-process on `127.0.0.1`, port 0, and stop before return.

Component READMEs use purpose, Interface, Configuration, Secrets, Deploy, Verify,
Rollback, Security notes; modules start `Module: <name> (optional)`. Audit uses the shared
UTC JSON actor/team/target/outcome contract; metric prefix is `aa_`. No prompts, payloads,
tokens or file contents in logs.

## Tests and gates

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

[CI](tools/ci/README.md) runs offline component suites. Unit command:
`python -m unittest discover -s <component>/tests -t <component>`. Components with
`requirements-test.txt` use their documented pinned environment/pytest command. Shell:
`bash <component>/tests/test-*.sh` via Git Bash/WSL. Component READMEs define mock e2e,
template and promtool suites; validation uses kubeconform/CEL/rule checks when installed.
Disclose missing validators. Exercise canonical fixture scopes, important denial cases,
deterministic rerendering and enabled/disabled modules, including all-module rendering.

Live `check_*.py` or `tests/live/` checks take `--kubeconfig`, default to server dry-run
and are excluded from offline CI. Authorised platform admins execute pilot checks; CI
never mutates clusters. [Sanitization](tools/sanitize/README.md) is a release gate: no
personal identifiers, source history, private topology, real secrets, live reports or
generated evidence. Examples/fixtures use synthetic Example Org data only.

## Pull request checklist

- Explain trigger/result and affected identity/account/data boundary.
- Regenerate outputs from authored changes; review deterministic diffs.
- Include checks, explicit skips and unresolved VERIFY gates.
- Preserve R1-R5, default-deny, runtime isolation, pins and narrow credentials.
- Test denial paths; plan server dry-runs for changed policy.
- Update component docs, required Secrets and runbook rollback.
- Pass links/anchors and sanitization with no personal or sensitive evidence.
- Add an ADR for boundaries, contracts or default-policy changes.

## ADR process and VERIFY

Use the next number in [ADRs](docs/adr/README.md), with context, decision and consequences;
update the index and explicitly supersede previous decisions. Explain alternatives briefly.
New vendors, broker auth and relaxed isolation need platform/security review.

VERIFY is a fact that cannot yet be relied on. Clear it with reproducible evidence against
pinned binary/chart and target cluster, including denial tests for security behavior.
Record non-sensitive evidence in the component Verify section; keep unsupported paths
disabled. Documentation or readiness alone cannot establish runtime enforcement.

Cross-component interfaces are defined in [contracts](docs/CONTRACTS.md).

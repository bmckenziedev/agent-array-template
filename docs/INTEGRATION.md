# Integration verification

Phase 2 closes the Part A owner fixes in the authored production tree. The workstation
window defaults are quoted; the example and synthetic all-module configurations render.
No live cluster, vendor login, source-repository mutation, commit or push was performed.
The parallel supervisor owns all changes under sessions and services/supervisor; its
remaining integration requirements are recorded below rather than weakening the gates.

## Completed owner fixes

Item | Owner files / proof
--- | ---
Workstation time defaults | modules/workstation-lane/org.component.defaults.yaml; strict-subset regression in tests/test_templates.py
Login storage isolation | cluster/render_plugin.py; cluster/tests/test_contract.py
Required Secret conditions | argocd/secrets.required.yaml; services/farm-mcp/secrets.required.yaml; modules/wazuh/secrets.required.yaml; owner tests
Headlamp DNS | portal/headlamp/k8s/identity-egress.tmpl.yaml; portal/headlamp/tests
Wazuh overlay placement | modules/wazuh/README.md documents pinned manual assembly: strategic merge/delete fragments require private TLS/bcrypt inputs and cannot be applied as standalone manifests; tools/ci/rendered_assets.py validates exact assets and unknown manifests still fail
Native YAML placement | cluster/oidc/README.md and ops/audit/README.md retain consumer-native configuration; exact path/shape validation excludes these from resource application. Actual Velero objects moved to ops/velero/k8s with backend gate and manual Argo coverage
Metrics Service inventory | platform/sealed-secrets/k8s/metrics-service.tmpl.yaml supplies an explicit uniquely selected Service; matching monitor in monitoring/k8s/monitors; monitoring-only ingress and exactly one Argo owner
Session job gateway label | modules/session-jobs/task-job.template.yaml already carries llm-client; modules/session-jobs/tests/test_templates.py now checks it
Argo chart 10.9.6 | argocd/README.md records exact upstream chart paths; argocd/helm/argocd/values.tmpl.yaml quotes admin.enabled; argocd/tests/test_gitops.py
Audit and CONNECT alerts | ops/audit/audit-policy.tmpl.yaml preserves metadata/annotations without CONNECT bodies; modules/wazuh/audit/wazuh-k8s-audit-rules.tmpl.xml and tests include VAP Audit violations and break-glass CONNECT
Factory self-test | modules/factory/bench/harness/gates.py stages readable isolated fixtures and accounts for skips separately; tests cover missing prerequisites; workflow installs Node and pinned JS dependencies
Adoption boundary | AGENTS.md Do not bullet and docs/ADOPTION.md Side-by-side adoption section
CI/image failures | workflows install gate dependencies, preserve strict scalar quoting and use synthetic all-module pins; images publish only with explicit registry configuration; farm Dockerfile has a pinned base default; shell findings repaired in owned files

## Needs from other tasks

The orchestrator must update external common-org/reference/task contracts for corrected
vendor evidence and optional git identity. Those scratch files remain outside this tree.

## Needs: sessions

- In `sessions/tests/test-entrypoints.sh`, quote the complete array element
  `"NO_PROXY=127.0.0.1,localhost"`. ShellCheck SC2054 currently treats its comma as an
  array separator. Run the workflow's `shellcheck -x` invocation after the change.
- The parallel supervisor render currently emits supervisor containers from
  `sessions/*/sts-supervised/k8s/*/statefulset.per-user-tool.tmpl.yaml`, but their image
  is absent from the generated session shape admission policy. Include the exact
  configured supervisor image and complete supervised container/volume/token shape
  in the policy producer, retain unsupervised variants, and add positive/negative
  admission-shape tests. Do not weaken the image check in `tools/ci/check_contracts.py`.
- Resolved by the parallel session owner: fixture keys now merge supervisor defaults.
  The final offline runner passes sessions and supervisor suites. The renderer's
  forge-identity test discovers and checks both variants. This does not resolve the
  separate production admission-shape contract above.

## Phase 2 checks

The local run used Ubuntu and Python 3.12, requirements-dev.txt, Node 22, the pinned
engine JS lockfile and verified kubeconform/promtool archives. Both backend gate states
also ran in the backup suite. CI is still blocked only by the excluded session admission
shape and ShellCheck findings under Needs: sessions.

Check | Result
--- | ---
Example render / all-module fixture render | Both exit 0
Placeholder lint | Exit 0
Manifest validation, example | 331 valid, 0 invalid, 0 errors, 0 skipped
Manifest validation, all modules | 447 valid, 0 invalid, 0 errors, 0 skipped
Rendered contracts | Both retain 4 errors: supervisor image absent from session shape; excluded owner fix required
CEL, example / all modules | Both 0 fail
Promtool, example / all modules | 13 / 20 rendered rule files checked; all applicable tests SUCCESS. Disabled example rule producers explicitly skip their tests
Deterministic rerender, example / all modules | Both --check exit 0
Strict synthetic all-module render / rerender | Both exit 0; synthetic pins are offline shape evidence only
Strict example negative | Exit 2 as expected: all-zero artifact pins and automatic home-node selection refused
Complete offline runner | Exit 0, all 54 suites PASS; individual optional-tool skips remain explicit in output
Nine image builds | All PASS, local build only, no registry publishing
ShellCheck -x | One excluded sessions SC2054 warning remains; all owned shell files pass
PowerShell syntax | PASS with local Windows PowerShell parser; pwsh is unavailable locally
Linkcheck | 0 fail
Repository sanitizer | 0 fail, 19 warnings from synthetic test dates
Make | Unavailable locally; render/test/validate/sanitize/docs recipes executed directly with the documented Python/Bash commands

Commands ran from the repository root:

```sh
python -m pip install -r requirements-dev.txt
npm ci --ignore-scripts --prefix modules/factory/engine/js
python tools/ci/run_tests.py
python tools/render/render.py --org org/org.example.yaml --out <example-output>
python tools/ci/module_config.py --out <all-module-org>
python tools/render/render.py --org <all-module-org>/org.example.yaml --out <all-output>
python tools/render/render.py --lint-placeholders .
# Each tree used the configured v1.36.5+k3s1 version (validator normalizes it).
bash tools/ci/validate_manifests.sh <tree> v1.36.5+k3s1
python tools/ci/check_contracts.py --rendered <tree>
python tools/ci/lint_cel.py <tree>
bash tools/ci/promtool_tests.sh <tree>
python tools/render/render.py --org <org> --out <tree> --check
python tools/ci/module_config.py --strict-fixture --out <strict-org>
python tools/render/render.py --org <strict-org>/org.example.yaml --strict --out <strict-output>
python tools/render/render.py --org <strict-org>/org.example.yaml --strict --out <strict-output> --check
python tools/render/render.py --org org/org.example.yaml --strict --out <negative-output>
python tools/ci/linkcheck.py --root .
python tools/sanitize/scan.py --root .
shellcheck -x <all-shell-files>
powershell -NoProfile -File tools/ci/check_powershell.ps1
docker build --progress=plain -f <each-of-nine-Dockerfiles> .
```

## Suite table

Suite | Result | Detail
--- | --- | ---
argocd | PASS |
cluster | PASS |
cluster/tests/test-prepare-login-root.sh | PASS |
llm | PASS |
mcp | PASS |
mcp/servers/_template | PASS |
mcp/servers/arrayops | PASS |
modules/arc-ci | PASS |
modules/factory | PASS |
modules/factory/api | PASS |
modules/factory/bench | PASS |
modules/factory/engine | PASS |
modules/factory/index | PASS |
modules/factory/mcp | PASS |
modules/factory/queue | PASS |
modules/gpu-lanes | PASS |
modules/gpu-lanes/tests/test-grow-root-lv.sh | PASS |
modules/hermes-ops-chat | PASS |
modules/hostguard | PASS |
modules/hostwatch | PASS |
modules/hostwatch/tests/test-hostwatch.sh | PASS |
modules/k3s-baremetal/tests/test-scripts.sh | PASS |
modules/k3s-maintenance | PASS |
modules/k3s-maintenance/tests/test-window.sh | PASS |
modules/pkg-mirror | PASS |
modules/seccomp-gvisor | PASS |
modules/session-jobs | PASS |
modules/wazuh | PASS |
modules/workstation-lane | PASS |
monitoring | PASS |
monitoring/tests/test-seal-alertmanager-urls.sh | PASS |
ops/audit | PASS |
ops/backup | PASS |
ops/backup/tests/test-preview.sh | PASS |
platform/hardening | PASS |
platform/kata | PASS |
platform/kata/tests/test-install-kata.sh | PASS |
platform/kata/tests/test-kata-gc.sh | PASS |
platform/sealed-secrets | PASS |
platform/sealed-secrets/tests/test-seal-registry-pull.sh | PASS |
platform/workload-templates | PASS |
portal/access | PASS |
portal/headlamp | PASS |
portal/panel | PASS |
services/farm-mcp | PASS |
services/pace | PASS |
services/supervisor | PASS |
sessions | PASS |
sessions/kimi/image | PASS |
sessions/tests/test-entrypoints.sh | PASS |
tools/aa | PASS |
tools/ci | PASS |
tools/render | PASS |
tools/sanitize | PASS |

## Finding record

The table retains Part A scope: not-applicable supervisor rows describe its original
snapshot. Phase 2 closes every deferred owner row; parallel supervisor validation is
tracked separately and is not inferred from Part A findings.

Finding | Status | Files / evidence | Outcome / owner
--- | --- | --- | ---
R01 | fixed | argocd/k8s/projects.tmpl.yaml | services cluster kinds restricted to ClusterRole/ClusterRoleBinding.
R02 | fixed | argocd/k8s/apps/teams-appset.tmpl.yaml; argocd/k8s/apps/05-org-directory.tmpl.yaml | Team and aa global coverage.
R03 | fixed | tools/aa/aa_cli/kube.py; tools/aa/tests | Deleted per-user cluster reader; immutable-subject namespace derivation.
R04 | fixed | platform/hardening/render_plugin.py | Control-plane allow sets, node scrapes and monitoring ingress.
R05 | fixed | cluster/render_plugin.py; cluster/tests/test_contract.py | ConfigMap and API egress policy moved to isolated login-storage namespace.
R06 | fixed | portal/headlamp/k8s/identity-egress.tmpl.yaml; portal/headlamp/tests | CoreDNS namespace and Pod selectors replace DNS Service ipBlock.
R07 | fixed | services/pace/k8s/reader.per-team.tmpl.yaml; services/pace/tests/test_templates.py | pace:8080 matches proxy URL/RBAC.
R08 | fixed | services/pace/render_plugin.py; sessions/render_plugin.py | Endpoint and control-plane overlay ingress peers.
R09 | fixed | argocd/k8s/apps/users-appset.tmpl.yaml; sessions/suspend/render_plugin.py; docs/runbooks | Replicas ignored/respected; zero-pod suspended quota and scale-to-zero runbook.
R10 | fixed | sessions/k8s/aa-session-writers.tmpl.yaml | Narrow controller/provisioner exceptions.
R11 | fixed | sessions/k8s/aa-session-exec.tmpl.yaml | Part A portforward CONNECT guard; Part B socket integration skipped, supervisor absent.
R12 | fixed | sessions/docs/exec-guard-webhook.md; sessions/k8s/20-lookup-webhook.tmpl.yaml | VERIFY correction supersedes CONNECT webhook: namespace-ticket VAP, storage-only TLS webhook with https Service port.
R13 | fixed | tools/ci/rendered_assets.py; cluster/oidc/README.md; ops/audit/README.md; ops/velero/k8s; modules/wazuh/README.md; modules/wazuh/tests/test_assets.py | Native config and incomplete strategic patches validated as exact assets; actual Velero manifests moved to ordinary gated GitOps.
R14 | fixed | tools/render/aa_render/lint.py; tools/render/tests | Placeholder lint scope excludes fixtures/docs/tests.
R15 | fixed | tools/ci/run_tests.py; tools/ci/tests | Component cwd and importlib pytest mode.
R16 | fixed | tools/ci/run_tests.py | Production/test requirements installed separately; hashes preserved. Linux pinned Python dependency suites pass.
R17 | fixed | sessions/common/bin/aa-mcp-bridge; sessions/tests/test_mcp_bridge.py; mcp/render_plugin.py | Bounded stdio bridge rereads tokens. Pinned CLI deployment VERIFY remains.
R18 | not applicable | docs/CONTRACTS.md; docs/SUPERVISION.md | Part B: supervisor absent; current spec is holder-only spawn, never break-glass spawn.
R19 | not applicable | docs/CONTRACTS.md | Part B: absent. Current amended spec preserves separate PID namespaces and same UID, superseding older distinct-UID draft.
R20 | not applicable | docs/CONTRACTS.md | Part B: absent; transcript-only subPaths documented for future integration.
R21 | not applicable | docs/CONTRACTS.md | Part B: absent; supervisor image/container/audience policy must land with that build.
R22 | not applicable | docs/SUPERVISION.md | Part B: absent; no console credentials in session pods; external relay extension documented.
R23 | not applicable | docs/CONTRACTS.md | Part B: absent; permission shim/managed MCP entry requires supervisor implementation.
R24 | not applicable | docs/INTEGRATION.md | Part B supervisor proof not run. Example zero digests deliberately fail strict mode.
R25 | fixed | argocd/render_plugin.py; argocd/tests/test_gitops.py; docs/CONTRACTS.md | Raw application groups, prefixed Kubernetes groups; chat uses directory teams.
R26 | fixed | tools/render/aa_render/model.py; tools/render/tests/test_render.py; org/README.md | username_claim=sub validation and negative test.
R27 | fixed | tools/render/aa_render; tools/render/tests; tools/render/README.md | Defaults, string comparisons, missing-path false, gated import refusal, collision failure and root-relative registries.
R28 | fixed | platform/sealed-secrets/k8s/metrics-service.tmpl.yaml; platform/sealed-secrets/render_plugin.py; monitoring/k8s/monitors/sealed-secrets.servicemonitor.tmpl.yaml; argocd/tests/test_gitops.py | Explicit metrics Service/port/selector with monitor in kube-system, monitoring ingress and exactly one Argo owner.
R29 | fixed | modules/session-jobs/tests/test_templates.py; modules/session-jobs/task-job.template.yaml | Existing source Pod llm-client label covered by regression.
R30 | fixed | services/pace/pace.py; services/pace/tests/test_service.py; monitoring/alerts | Account-info metric and account joins.
R31 | fixed | llm/teams_sync.py; llm/k8s/usage-report.tmpl.yaml; services/pace; tools/render/aa_render/model.py | Account-scoped monthly spend reporter; gateway writer/ingress; otel warning. Unlimited pools have no pacing windows to update.
R32 | fixed | modules/factory/k8s/factory.rules.tmpl.yaml; modules/factory/DESIGN.md; monitoring/render_plugin.py | Factory owns factory alert source; monitoring emission gated off; metric names documented.
R33 | fixed | modules/factory/engine/render_plugin.py; modules/factory/engine/factory_engine/config.py; modules/factory/tests | Registry lane names/endpoints; replacement avoids retaining static phantom lanes and preserves routing ladder semantics.
R34 | fixed | mcp/render_plugin.py; mcp/tests/test_render.py | Factory API URL follows module gate.
R35 | already satisfied | sessions/common/bin/aa-mcp-token:14 | JSON Authorization string map; new sessions/tests/test_auth_helpers.py verifies exact shape.
R36 | fixed | sessions/common/bin/aa-lease; sessions/claude/image/bin/aa-rc; sessions/codex/image/bin/aa-codex; sessions/tests/test_pace_helpers.py | Foreground hold, 60-second renewal, release on child exit/start failure.
R37 | fixed | portal/panel/app/identity.py; portal/panel/tests/test_panel.py | Cloudflare email mapping with distinct-subject test.
R38 | fixed | modules/hermes-ops-chat | Named chat-to-slug Secret and directory-team authorization.
R39 | fixed | modules/arc-ci; argocd/k8s/apps/modules/arc-ci | Project-prefixed namespace names; dynamic install project argument; Kubernetes interface release names preserved.
R40 | fixed | platform/kata/k8s/runtimeclass.tmpl.yaml; tools/render/aa_render/model.py; tools/render/tests | Configured runtime selector and node-runtime negative validation.
R41 | fixed | sessions/k8s/aa-session-kinds.tmpl.yaml | CREATE denylist; registry-named forge Secret exception preserves credential boundaries.
R42 | fixed | secrets/README.md; platform/sealed-secrets/tests/test_seal.py | Registry pull credential producer seals independently in each user namespace with strict scope; login storage never copied.
R43 | fixed | tools/sanitize/scan.py; tools/sanitize/tests; tools/sanitize/README.md | Sealed-ciphertext prohibition only at export gate; adoption CI unchanged.
R44 | fixed | tools/render/aa_render/cli.py; monitoring/render_plugin.py; tools/ci/promtool_tests.sh; monitoring/alerts/tests | Plain rule extraction and cross-module suite discovery; Phase 2 promtool executes the production rules and configured test expressions successfully.
R45 | fixed | tools/ci/validate_manifests.sh | Version normalisation, missing-schema fallback and zero-resource refusal.
R46 | fixed | tools/ci/lint_cel.py; platform/hardening/cel_guards.py; sessions/tests/test_templates.py; modules/hermes-ops-chat/k8s/admission.tmpl.yaml | Shared optional-field checker; Hermes missing guard repaired. Both production-tree configurations now pass CEL lint; live admission proof remains separate.
R47 | not applicable | docs/SUPERVISION.md | Part B: supervisor absent; metrics transport awaits its build.
R48 | not applicable | docs/CONTRACTS.md | Part B: supervisor absent; per-user policy producer awaits its build.
R49 | not applicable | tools/render/aa_render; docs/CONTRACTS.md | Part B: supervisor variants absent; renderer supports gating semantics.
R50 | not applicable | docs/CONTRACTS.md | Part B: console egress settings await supervisor build.
R51 | not applicable | tools/render/aa_render/model.py; tools/render/tests/test_render.py | Part B: supervisor Dockerfile absent, matrix not added. Generic strict C_*_IMAGE zero-digest validation implemented/tested.
R52 | fixed | .gitignore; component READMEs | Requested strays removed after useful deployment notes folded into READMEs; .git retained untouched.
R53 | fixed | modules/wazuh/k8s/namespace.tmpl.yaml; modules/wazuh/overlay/namespace.tmpl.yaml; modules/wazuh/tests/test_audit.py | Prune=false,Delete=false protection on module namespace and overlay patch.
R54 | fixed | argocd/README.md | Manual bootstrap runbook; advisory Application waves.
R55 | fixed | sessions/docs/exec-guard-webhook.md; sessions/README.md | Guide moved out of k8s; links checked.
R56 | fixed | sessions/render_plugin.py; sessions/tests/test_render_plugin.py; docs/CONTRACTS.md | Constant reserved CIDRs appended; non-private node LAN /32 adoption note.
R57 | fixed | docs/CONTRACTS.md | Accepted public directory/account metadata and client-side proxy filtering documented.
R58 | fixed | mcp/render_plugin.py; mcp/k8s/server/networkpolicy.per-mcp.tmpl.yaml | NetworkPolicy objects use mcp-egress-<server> for user and server outputs.

## Vendor evidence and additions

Claude managed loading/exclusivity is documented; MCP CLI-flag fallback was removed.
Codex native bearer/workspace facts are confirmed; requirements pin the allowed workspace
only when nonempty and managed sandbox_mode is removed. The rotating-token bridge retains
its deployment VERIFY. Kimi remains v1 MCP-disabled with wrapper/hook enforcement and a
plain-login fallback note. LiteLLM removes key_type on mint keys, uses OSS user membership,
gates enterprise licence, and exposes monitoring-only port 4001. Cilium entity API egress
and k3s startup/stale-iptables caveats are documented. VAP CONNECT replaces the old
CONNECT webhook; audit-owner alert integration is implemented offline. Live dry-runs remain deployment work.
Argo stateful replica controls and v3 grants are present; Helm chart 10.9.6 key paths are now verified in argocd/README.md. Current supervisor amendments override the older shared-PID proposal.

Optional git identity validates named Secret/provider/username, normalises into users.json
and all fixture copies, mounts only into the CLI, and uses an HTTPS-host-confined helper.
Coverage additions are recorded in architecture, multi-user, context and AGENTS docs.

## Phase 2 test summary lines

The complete offline runner emitted these summary lines (in suite execution order).
Optional runtime prerequisites remain explicit skips, never successful gate evidence.

```text
Ran 11 tests in 0.774s
OK
6 passed in 0.27s
prepare-login-root: 3 cases passed
Ran 21 tests in 6.339s
OK
15 passed in 0.37s
12 passed in 0.13s
7 passed in 0.16s
Ran 4 tests in 0.209s
OK
2 passed in 0.38s
19 passed in 13.38s
8 passed in 12.06s
Ran 55 tests in 9.337s
OK (skipped=1)
13 passed in 1.11s
6 passed in 1.85s
15 passed in 32.20s
Ran 7 tests in 0.127s
OK
PASS: DCGM references and 22 unique counters
PASS: dry run, apply, target guards, root guard, and safety reserve
Ran 5 tests in 0.066s
OK
7 passed in 0.13s
Ran 2 tests in 0.008s
OK
154 assertions passed, 0 failed
k3s-baremetal: 8 cases passed
Ran 11 tests in 0.074s
OK
Maintenance window refusal: OK
3 passed in 0.16s
Ran 26 tests in 0.092s
OK
Ran 20 tests in 5.593s
OK
Ran 16 tests in 0.153s
OK
Ran 10 tests in 0.046s
OK
Ran 20 tests in 0.551s
OK
PASS: receiver Secret naming, no plaintext output/argv, fail-closed sealing
Ran 18 tests in 0.087s
OK
Ran 24 tests in 19.123s
OK
Backup shell preview/refusal: OK
Ran 17 tests in 1.483s
OK
Ran 1 test in 0.006s
OK
PASS: 14 checks (4.2 bundles, runtime-rs and Go paths, configs, safety overrides, failure path)
PASS: 156 checks (orphan removal; kept: crictl/ctr/shim/VMM/socket/pid/young/mounted/unclear; dry-run; missing and empty /run; inventory and removal failures; metrics; lock)
Ran 11 tests in 0.065s
OK
PASS: registry credentials stay in stdin; fake kubeseal on PATH; strict scope output.
Ran 6 tests in 0.158s
OK
Ran 7 tests in 4.909s
OK
1 passed in 0.08s
Ran 22 tests in 0.648s
OK
20 passed in 0.19s
Ran 29 tests in 23.241s
OK
36 passed in 1.82s
Ran 68 tests in 8.533s
OK
3 passed in 0.13s
Ran 47 tests in 10.767s
OK
24 passed in 1.98s
99 passed in 1.75s
11 passed in 0.12s
```

## Image build summaries

Image | Result
--- | ---
session-claude | PASS
session-codex | PASS
session-kimi | PASS
panel | PASS
session-runner | PASS
pace | PASS
farm-mcp | PASS
factory | PASS
hermes-chat | PASS

Factory benchmark self-test: 17 passed, 0 failed, 0 skipped. Without Node, 8 passed,
0 failed, 3 skipped with explicit prerequisite reasons; the matching unit suite reports
its prerequisite test skip explicitly. Read-only first-push Actions failure logs were
inspected before changing workflows (CI run 37378951269; Images run 37378951360).

## Files written and mapping

Owner fixes are new work on exported files; no source-repository files were read or copied.
The old Velero template was removed after moving the actual resource to ops/velero/k8s.
No placeholder keys were invented; existing C_/M_ defaults and org keys were consumed.
Kubernetes/Helm semantics, per-user sealing, account boundaries and CI contracts retain
their existing interfaces. Shell files retain their existing executable requirements;
new Python helpers are invoked explicitly with Python. No possible live secrets were seen.

Sanitization self-check command: `python CODEX/sanitize_selfcheck.py <every-existing-touched-path>`.
Result: **0 fail, 15 warn**. Ten warnings are synthetic fixture dates in hostwatch/audit
tests. Five concern certificate-marker or sealed-field recognition strings in mock
sealing tests; no certificate bodies or real ciphertext are present. Repository scanning
reports 19 synthetic-date warnings across the whole tree. Generated dependency/cache directories and temporary root scripts were removed.
Live admission, Wazuh delivery, vendor CLI and cluster/CNI VERIFY gates remain unproven;
none was cleared using offline tests or image builds.

```text
.github/workflows/ci.yml
.github/workflows/images.yml
AGENTS.md
argocd/README.md
argocd/helm/argocd/values.tmpl.yaml
argocd/k8s/apps/backup/85-backup-policy.tmpl.yaml
argocd/k8s/apps/backup/RENDER-IF
argocd/k8s/projects.tmpl.yaml
argocd/secrets.required.yaml
argocd/tests/test_gitops.py
cluster/oidc/README.md
cluster/render_plugin.py
cluster/tests/test_contract.py
docs/ADOPTION.md
docs/INTEGRATION.md
modules/factory/bench/README.md
modules/factory/bench/harness/gates.py
modules/factory/bench/tests/test_gates.py
modules/gpu-lanes/tests/test-grow-root-lv.sh [existing executable]
modules/hostwatch/tests/test-hostwatch.sh [existing executable]
modules/k3s-baremetal/10-stage.sh [existing executable]
modules/k3s-baremetal/20-overlay.sh [existing executable]
modules/k3s-baremetal/30-install.sh [existing executable]
modules/k3s-baremetal/40-firewall.sh [existing executable]
modules/k3s-baremetal/50-node-contract.sh [existing executable]
modules/k3s-baremetal/60-verify.sh [existing executable]
modules/k3s-baremetal/lib.sh [existing executable]
modules/k3s-maintenance/00-preflight.sh [existing executable]
modules/k3s-maintenance/10-backup.sh [existing executable]
modules/k3s-maintenance/20-secrets-encryption.sh [existing executable]
modules/k3s-maintenance/30-audit-logging.sh [existing executable]
modules/k3s-maintenance/40-node-maint.sh [existing executable]
modules/k3s-maintenance/41-host-reboot.sh [existing executable]
modules/k3s-maintenance/42-host-postboot.sh [existing executable]
modules/k3s-maintenance/60-remove-stale-etcd-copy.sh [existing executable]
modules/k3s-maintenance/lib.sh [existing executable]
modules/seccomp-gvisor/tests/live/verify-threads.sh [existing executable]
modules/session-jobs/diff_bundler.sh [existing executable]
modules/session-jobs/entrypoint.sh [existing executable]
modules/session-jobs/tests/test_templates.py
modules/wazuh/README.md
modules/wazuh/audit/wazuh-k8s-audit-rules.tmpl.xml
modules/wazuh/k8s/namespace.tmpl.yaml
modules/wazuh/overlay/namespace.tmpl.yaml
modules/wazuh/secrets.required.yaml
modules/wazuh/tests/test_assets.py
modules/wazuh/tests/test_audit.py
modules/workstation-lane/tests/test_templates.py
monitoring/k8s/monitors/sealed-secrets.servicemonitor.tmpl.yaml
monitoring/tests/test-seal-alertmanager-urls.sh [existing executable]
ops/audit/README.md
ops/audit/audit-policy.tmpl.yaml
ops/audit/audit_log_probe.py
ops/audit/audit_policy_check.py
ops/audit/tests/test_audit_log_probe.py
ops/audit/tests/test_audit_policy.py
ops/backup/backup.sh [existing executable]
ops/backup/restore-drill.sh [existing executable]
ops/backup/tests/test_velero.py
ops/backup/velero/README.md
ops/backup/velero/login-resource-policy.tmpl.yaml
ops/backup/weekly-check.sh [existing executable]
ops/velero/README.md
ops/velero/RENDER-IF
ops/velero/k8s/login-resource-policy.tmpl.yaml
ops/velero/k8s/namespace.tmpl.yaml
platform/kata/install-kata.sh [existing executable]
platform/kata/kata-gc.sh [existing executable]
platform/kata/tests/test-install-kata.sh [existing executable]
platform/kata/tests/test-kata-gc.sh [existing executable]
platform/kata/verify-kata.sh [existing executable]
platform/sealed-secrets/README.md
platform/sealed-secrets/k8s/metrics-service.tmpl.yaml
platform/sealed-secrets/render_plugin.py
platform/sealed-secrets/tests/test_seal.py
portal/headlamp/k8s/identity-egress.tmpl.yaml
portal/headlamp/tests/__init__.py
portal/headlamp/tests/test_dns.py
secrets/README.md
services/farm-mcp/Dockerfile
services/farm-mcp/README.md
services/farm-mcp/k8s/deployment.tmpl.yaml
services/farm-mcp/secrets.required.yaml
services/farm-mcp/tests/test_farm.py
tools/ci/README.md
tools/ci/check_contracts.py
tools/ci/module_config.py
tools/ci/promtool_tests.sh [existing executable]
tools/ci/rendered_assets.py
tools/ci/tests/test_contracts.py
tools/ci/tests/test_workflows.py
tools/ci/validate_manifests.sh [existing executable]
tools/render/tests/test_render.py
```

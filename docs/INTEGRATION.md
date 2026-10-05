# Integration verification

Integration is incomplete. The production-tree example and all-module render both stop
at the unquoted time in workstation-lane defaults, outside the authorised write paths.
The same blocker prevents placeholder lint. Diagnostic renders used a temporary copy
with clearly identified pending owner fixes; they are not passing evidence for this tree.
No live cluster, vendor login, or source-repository mutation was performed. Part B skipped:
services/supervisor is absent. The existing .git directory was left untouched.

## Needs from other tasks

- Workstation-lane owner: quote window_stop at modules/workstation-lane/org.component.defaults.yaml:6.
- Cluster owner: move the generated login-local-path ConfigMap and old API policy to the new
  login-storage namespace in cluster/render_plugin.py. Templates and isolated baseline are ready.
- Argo/farm-mcp/Wazuh owners: add required_when to their secrets.required.yaml; Wazuh also
  needs namespace_ref instead of namespace. The farm mint key declaration must agree on LITELLM_MINT_KEY.
- Portal headlamp owner: replace DNS Service-ipBlock with the CoreDNS peer selectors.
- Wazuh owner: its overlay Kubernetes YAML lands under files/ and is not ordinary GitOps
  manifests; move/assemble the overlay through the owning deployment path and protect Namespaces.
- Cluster/ops owners: kubeconfig, audit policy and Velero resource-policy YAML under files/
  has apiVersion/kind. The requested literal checker rejects these configuration documents;
  settle their output format with their consumers without deploying them as cluster resources.
- Sealed-secrets/monitoring owners: the chart-owned metrics Service is absent from the
  renderer's manifest inventory, so its ServiceMonitor cannot prove its selector/port contract.
- Session-jobs owner: add the llm-client label to its source job Pod template (panel runtime
  labels already carry it). Its source template is outside this integration's paths.
- Argo Helm owner: confirm chart 10.9.6 key paths and quote admin.enabled as "false".
- Audit/Wazuh owners: alert on VAP Audit-action events and break-glass CONNECT activity.
- Factory bench owner: isolated executable self-test references return runtime in WSL;
  its tempfile work directory is mode 0700 while the gate container runs as UID 65534.
  Preserve isolation; fix fixture staging/read permissions rather than treating failure as skip.
- Orchestrator: update external common-org/reference/task contracts for corrected vendor evidence
  and optional git identity. Those scratch files are outside the allowed WORK write paths.

## Checks

| Check | Result |
| --- | --- |
| Example render / all-module render | Blocked by defaults line 6 |
| Placeholder lint | Same blocker |
| Production rendered contracts / schema / CEL | Blocked because neither render exists |
| Diagnostic all-module CEL | 0 fail after final template refresh (temporary diagnostic copy only) |
| promtool | Absent; explicit tool skip, not passing rule evidence |
| Linkcheck | 0 fail |
| Sanitization | 0 fail; dated synthetic fixture warnings remain |
| Component suites | Table below; failures are retained, not relabelled as skips |

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
modules/factory/bench | FAIL | 
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
portal/panel | PASS | 
services/farm-mcp | PASS | 
services/pace | PASS | 
sessions | PASS | 
sessions/kimi/image | PASS | 
sessions/tests/test-entrypoints.sh | PASS | 
tools/aa | PASS | 
tools/ci | PASS | 
tools/render | PASS | 
tools/sanitize | PASS |

## Finding record

Finding | Status | Files / evidence | Outcome / owner
--- | --- | --- | ---
R01 | fixed | argocd/k8s/projects.tmpl.yaml | services cluster kinds restricted to ClusterRole/ClusterRoleBinding.
R02 | fixed | argocd/k8s/apps/teams-appset.tmpl.yaml; argocd/k8s/apps/05-org-directory.tmpl.yaml | Team and aa global coverage.
R03 | fixed | tools/aa/aa_cli/kube.py; tools/aa/tests | Deleted per-user cluster reader; immutable-subject namespace derivation.
R04 | fixed | platform/hardening/render_plugin.py | Control-plane allow sets, node scrapes and monitoring ingress.
R05 | deferred | cluster/k8s/login-storage; cluster/login-storage; platform/hardening | Templates, isolated baseline and helper hostPath guard fixed; cluster owner must move generated ConfigMap in cluster/render_plugin.py outside scope.
R06 | deferred | sessions; mcp; services/pace; llm; modules/gpu-lanes | Owned DNS peers fixed; portal/headlamp owner must fix its excluded template.
R07 | fixed | services/pace/k8s/reader.per-team.tmpl.yaml; services/pace/tests/test_templates.py | pace:8080 matches proxy URL/RBAC.
R08 | fixed | services/pace/render_plugin.py; sessions/render_plugin.py | Endpoint and control-plane overlay ingress peers.
R09 | fixed | argocd/k8s/apps/users-appset.tmpl.yaml; sessions/suspend/render_plugin.py; docs/runbooks | Replicas ignored/respected; zero-pod suspended quota and scale-to-zero runbook.
R10 | fixed | sessions/k8s/aa-session-writers.tmpl.yaml | Narrow controller/provisioner exceptions.
R11 | fixed | sessions/k8s/aa-session-exec.tmpl.yaml | Part A portforward CONNECT guard; Part B socket integration skipped, supervisor absent.
R12 | fixed | sessions/docs/exec-guard-webhook.md; sessions/k8s/20-lookup-webhook.tmpl.yaml | VERIFY correction supersedes CONNECT webhook: namespace-ticket VAP, storage-only TLS webhook with https Service port.
R13 | deferred | portal/access/*/k8s; argocd/k8s; tools/ci/check_contracts.py | Owned ingress manifests moved and destinations/coverage fixed; Wazuh overlay and native config YAML outputs require excluded owners.
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
R28 | deferred | monitoring/k8s/monitors/farm-mcp.tmpl.yaml; tools/ci/check_contracts.py | Owned Service labels/8080 names checked; farm namespace fixed; chart-owned sealed-secrets metrics Service inventory still needs owner resolution.
R29 | deferred | llm/render_plugin.py; llm/k8s/usage-report.tmpl.yaml | Dedicated monitoring metrics ingress fixed; Hermes label exists. Session-jobs source Pod template is outside scope and needs llm-client label.
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
R42 | deferred | sessions/README.md | Per-user/cluster-wide sealing or node-provider recipe documented; secrets README/producer is outside scope, needs secrets owner.
R43 | fixed | tools/sanitize/scan.py; tools/sanitize/tests; tools/sanitize/README.md | Sealed-ciphertext prohibition only at export gate; adoption CI unchanged.
R44 | fixed | tools/render/aa_render/cli.py; monitoring/render_plugin.py; tools/ci/promtool_tests.sh; monitoring/alerts/tests | Plain rule extraction and cross-module suite discovery; promtool absent, runtime proof skipped explicitly.
R45 | fixed | tools/ci/validate_manifests.sh | Version normalisation, missing-schema fallback and zero-resource refusal.
R46 | fixed | tools/ci/lint_cel.py; platform/hardening/cel_guards.py; sessions/tests/test_templates.py; modules/hermes-ops-chat/k8s/admission.tmpl.yaml | Shared optional-field checker; Hermes missing guard repaired. Diagnostic proof is distinct from blocked production renders.
R47 | not applicable | docs/SUPERVISION.md | Part B: supervisor absent; metrics transport awaits its build.
R48 | not applicable | docs/CONTRACTS.md | Part B: supervisor absent; per-user policy producer awaits its build.
R49 | not applicable | tools/render/aa_render; docs/CONTRACTS.md | Part B: supervisor variants absent; renderer supports gating semantics.
R50 | not applicable | docs/CONTRACTS.md | Part B: console egress settings await supervisor build.
R51 | not applicable | tools/render/aa_render/model.py; tools/render/tests/test_render.py | Part B: supervisor Dockerfile absent, matrix not added. Generic strict C_*_IMAGE zero-digest validation implemented/tested.
R52 | fixed | .gitignore; component READMEs | Requested strays removed after useful deployment notes folded into READMEs; .git retained untouched.
R53 | deferred | platform/hardening; modules/arc-ci; sessions; cluster/login-storage | Owned Namespace annotations fixed; excluded Wazuh Namespace overlay owner must protect its namespace.
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
CONNECT webhook; live dry-runs and audit-owner alert integration remain deployment work.
Argo stateful replica controls and v3 grants are present; excluded Helm chart keys still
need owner verification. Current supervisor amendments override the older shared-PID proposal.

Optional git identity validates named Secret/provider/username, normalises into users.json
and all fixture copies, mounts only into the CLI, and uses an HTTPS-host-confined helper.
Coverage additions are recorded in architecture, multi-user, context and AGENTS docs.

Focused reruns after the complete suite: sessions 55, renderer 99, MCP 15, Argo 9, CI 19, panel 22, hardening 17 and LiteLLM 21 tests passed. Argo has one explicit kubeconform-unavailable skip. Diagnostic deterministic rerender and placeholder lint exited 0; the diagnostic contract checker retained 21 owner-dependent errors. These results do not replace the blocked production-tree renders.

The kube-system privileged PSA change requires a platform-owner host-access compensating policy and live positive/negative verification before deployment.

Final checks: linkcheck 0 fail; repository sanitizer 0 fail (19 synthetic-date warnings); CODEX sanitize_selfcheck over the entire tree, including every touched file, 0 fail (53 warnings). Temporary render directories and generated dependency/cache directories were removed. The .git directory remains untouched.

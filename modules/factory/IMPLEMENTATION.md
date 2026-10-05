# Report: task-org-factory

## Files written

All output is under `modules/factory/`. The tree contains 150 files. Executable
markers identify entries whose Git executable bit the orchestrator should set.
The private pinned source snapshot, npm dependencies and test caches were removed.

```text
DESIGN.md
Dockerfile
IMPLEMENTATION.md
README.md
api/README.md
api/factory_api/__init__.py
api/factory_api/auth.py
api/factory_api/metrics.py
api/factory_api/service.py
api/tests/__init__.py
api/tests/test_api.py
api/tests/test_metrics.py
api/tests/test_store_integration.py
bench/README.md
bench/bench.py  [executable]
bench/examples/toy-repo/arithmetic.cjs
bench/examples/toy-repo/collection.cjs
bench/examples/toy-repo/labels.cjs
bench/examples/toy-repo/package.json
bench/examples/units.json
bench/harness/__init__.py
bench/harness/gates.py
bench/harness/production.py
bench/harness/profile.py
bench/harness/runner.py
bench/models.json
bench/tests/__init__.py
bench/tests/test_gates.py
bench/tests/test_runner.py
engine/README.md
engine/docker/Dockerfile.tools
engine/examples/lanes.cluster.json
engine/examples/template.example.json
engine/factory.py  [executable]
engine/factory_engine/__init__.py
engine/factory_engine/__main__.py
engine/factory_engine/bundle.py
engine/factory_engine/cards.py
engine/factory_engine/cli.py
engine/factory_engine/client.py
engine/factory_engine/config.py
engine/factory_engine/engine.py
engine/factory_engine/envelope.py
engine/factory_engine/fsutil.py
engine/factory_engine/gates.py
engine/factory_engine/jsbridge.py
engine/factory_engine/lanes.py
engine/factory_engine/packer.py
engine/factory_engine/scheduler.py
engine/factory_engine/schema.py
engine/factory_engine/snapshot.py
engine/factory_engine/store.py
engine/factory_engine/tokens.py
engine/js/.dockerignore
engine/js/gate.js
engine/js/inventory.js
engine/js/lib/docsplice.js
engine/js/lib/globals.js
engine/js/lib/jsdoc.js
engine/js/lib/modinfo.js
engine/js/lib/mutate.js
engine/js/lib/parse.js
engine/js/lib/scope.js
engine/js/lib/symbols.js
engine/js/lib/testcheck.js
engine/js/package-lock.json
engine/js/package.json
engine/profiles/jsdoc-cjs.json
engine/requirements-exact-tokens.txt
engine/requirements.txt
engine/schemas/card.v1.schema.json
engine/schemas/lanes.v1.schema.json
engine/schemas/profile.v1.schema.json
engine/schemas/template.v1.schema.json
engine/tests/__init__.py
engine/tests/fixtures/synthetic-mini/package.json
engine/tests/fixtures/synthetic-mini/profile.json
engine/tests/fixtures/synthetic-mini/src/score.js
engine/tests/helpers.py
engine/tests/test_envelope_client.py
engine/tests/test_fairness.py
engine/tests/test_gate_synthetic.py
engine/tests/test_generation_dispatch.py
engine/tests/test_lanes_store.py
engine/tests/test_pace_route.py
engine/tests/test_policy.py
engine/tests/test_schema.py
engine/tests/test_transport.py
index/README.md
index/estate_index/__init__.py
index/estate_index/__main__.py
index/estate_index/build.py
index/estate_index/graph.py
index/estate_index/jsparse.py
index/estate_index/pack.py
index/estate_index/queries.py
index/estate_index/reposmd.py
index/estate_index/resolve.py
index/estate_index/schema.sql
index/estate_index/tokens.py
index/requirements-test.txt
index/requirements.txt
index/tests/conftest.py
index/tests/fixtures/mini/synthetic/math/package.json
index/tests/fixtures/mini/synthetic/math/src/range.js
index/tests/fixtures/mini/synthetic/report/package.json
index/tests/fixtures/mini/synthetic/report/src/report.js
index/tests/fixtures/mini/synthetic/ui/package.json
index/tests/fixtures/mini/synthetic/ui/src/view.js
index/tests/test_parse.py
index/tests/test_safety.py
index/tests/test_synthetic.py
k8s/config.tmpl.yaml
k8s/deployment.tmpl.yaml
k8s/identity.tmpl.yaml
k8s/monitoring.tmpl.yaml
k8s/network.tmpl.yaml
k8s/service-storage.tmpl.yaml
mcp/README.md
mcp/factory_mcp/__init__.py
mcp/factory_mcp/__main__.py
mcp/factory_mcp/engine_client.py
mcp/factory_mcp/server.py
mcp/mcp.json.example
mcp/requirements-test.txt
mcp/requirements.txt
mcp/tests/conftest.py
mcp/tests/test_mcp.py
org.component.defaults.yaml
queue/README.md
queue/RUNBOOK.md
queue/factory_queue/__init__.py
queue/factory_queue/api_store.py
queue/factory_queue/legacy.py
queue/factory_queue/snapshot_queue.py
queue/factory_queue/worker.py
queue/prepare.py  [executable]
queue/queue.py  [executable]
queue/remote.py  [executable]
queue/service.py
queue/supervisor.py  [executable]
queue/tests/__init__.py
queue/tests/test_api_store.py
queue/tests/test_snapshot_queue.py
queue/tests/test_worker.py
render_plugin.py
secrets.required.yaml
tests/__init__.py
tests/fixtures/org.fixture.json
tests/test_templates.py
```

## Source mapping

Read-only source revision: `3704483e719429d1e246cb6f05c7770375560818`.

| Source | Export |
| --- | --- |
| factory/engine/factory_engine, schemas, js and selected tests | engine: adapted portable engine, foreground runners and schema validation |
| factory/queue/queue.py, remote.py, prepare.py and regression semantics | queue: compatible bounded snapshot transfer machinery and new shared batch store/worker |
| factory/index/estate_index, schema.sql and tests | index: generic package discovery and optional repository map |
| factory/mcp/factory_mcp, locked requirements and tests | mcp: bounded stdio tools/resources with authenticated HTTP submission/results |
| factory/deploy manifest structure and metrics hooks | k8s and render_plugin.py: newly authored templates and endpoint expansion |
| bench harness concepts and engine documentation verifier | bench: new profile/client/gate framework with production documentation adapter |
| Selected governing rules, card/template schema and gates; factory metrics contract | DESIGN.md |
| New | api, team scheduler, migrations, metrics ledger, synthetic estates/profiles/units and Dockerfile |
| Excluded estate artifacts, profiles, unit corpora, mock answers and results | Dropped; every included fixture and benchmark corpus is newly synthetic |
| Feeder, proof files, platform-specific build/import flow and personal passages | Dropped |

## Placeholder keys used

```text
APISERVER_ENDPOINT_IP
APISERVER_PORT
APISERVER_SERVICE_IP
CLUSTER_DNS_IP
IMAGE_FACTORY
LABEL_PREFIX
M_FACTORY_LANE_CONCURRENCY
M_FACTORY_QUEUE_MAX_BATCHES
M_FACTORY_QUEUE_MAX_CARDS
M_FACTORY_QUEUE_MAX_PAYLOAD_BYTES
M_FACTORY_QUEUE_RETENTION_SECONDS
M_FACTORY_STARVATION_DISPATCHES
M_FACTORY_STORAGE_GIB
M_FACTORY_WORKER_POLL_SECONDS
NS_FACTORY
NS_LLM
NS_MODELS
NS_MONITORING
NS_PORTAL
NS_SYSTEM
OIDC_USERNAME_PREFIX
PROJECT_NAME
REGISTRY_PULL_SECRET
RUNTIME_CLASS_VM
STORAGE_CLASS_DEFAULT
USER_NS_PREFIX
APISERVER_ENDPOINT_IPS_JSON (render plugin)
```

Declared module defaults: queue_max_batches=32, queue_max_cards=100,
queue_max_payload_bytes=1048576, queue_retention_seconds=43200,
starvation_dispatches=20, lane_concurrency=1, worker_poll_seconds=1,
storage_gib=10. The corresponding keys are M_FACTORY_*; no new global keys
were introduced. The module is automatically gated by modules.factory.enabled.

## Contracts

- The section-7 factory batch API is served on factory-api:8080. Submission,
  lead approval, submitter/lead cancellation, team status/list and audit use one
  durable shared store. Actor identity is never accepted from request arguments.
- Pod TokenReview requires the project MCP audience, the session service account
  and a labelled user namespace. OIDC portal identities use API-server verification
  and immutable directory subjects; the default-audience OIDC path cannot admit
  service accounts. Suspended users and unentitled teams are denied.
- The organisation directory provides users, teams, estates and MCP entitlements.
  Estate classification and deny globs are authoritative. Approval freezes bounded
  bytes; the worker verifies the SHA-256 manifest before packing.
- Every generation, including retries on another lane, re-enters per-lane weighted
  deficit round robin. Priority is capped before sequence ordering. The actual
  global foreground worker is exercised over 1000 dispatches at the required 3:2
  share. Team caps, starvation and restricted-local gates are tested.
- The stdio factory MCP uses the projected token file and the factory HTTP API.
  Missing FACTORY_API_URL disables submission/results clearly. Repository output
  is bounded and marked as untrusted.
- The pace route helper consumes POST /v1/route, rejects seats and never routes
  restricted work. Production bounces remain human handbacks; there is no automatic
  seat invocation or background frontier process.
- Metrics use durable idempotent records, coherent read snapshots and interval
  union for generation occupancy. Retention requires successful telemetry ingestion
  first. Missing power observations remain unknown; no suspended-state gauge is
  fabricated. Bundle generation does not imply destination application.
- Workstation pc-pull/pc-return commands retain their privileged transfer contract.
  Returned output is independently gated. No feeder or workstation listener ships.
- The factory runs one supervised foreground service with joined worker threads.
  Session images consume index/MCP separately at /opt/factory.

## Tests

The requested commands were run from the export root. JavaScript dependencies
were installed with `npm ci --ignore-scripts --no-audit --no-fund` in engine/js
for the production gate tests, then removed from the deliverable.

| Command | Result |
| --- | --- |
| python -m unittest discover -s modules/factory/engine/tests -t modules/factory/engine | Ran 55 tests; OK, one optional system-interpreter jsonschema check skipped |
| temporary-venv/python -B -m unittest discover -s modules/factory/engine/tests -t modules/factory/engine -p test_schema.py | Ran 17 tests; OK, including the previously skipped jsonschema cross-check |
| python -m unittest discover -s modules/factory/queue/tests -t modules/factory/queue | Ran 15 tests; OK |
| python -B -m unittest discover -s modules/factory/queue/tests -t modules/factory/queue | Final bounded-read change: Ran 15 tests; OK |
| python -m unittest discover -s modules/factory/api/tests -t modules/factory/api | Ran 19 tests; OK |
| temporary-venv/python -m pytest -p no:cacheprovider modules/factory/index/tests modules/factory/mcp/tests -q | 19 passed |
| python -m unittest discover -s modules/factory/bench/tests -t modules/factory/bench | Ran 5 tests; OK |
| python modules/factory/bench/bench.py selftest | 5 units, 17 checks, 17 passed, 0 failed |
| python -B -m unittest discover -s modules/factory/tests -t modules/factory | Ran 2 tests; OK |

The temporary venv was created under TEMP. Its pinned dependency closure was
installed successfully with network access and revalidated by:

```text
temporary-venv/python -m pip install --require-hashes --no-deps -r modules/factory/index/requirements.txt -r modules/factory/index/requirements-test.txt -r modules/factory/mcp/requirements.txt -r modules/factory/mcp/requirements-test.txt
```

The pytest run set PYTHONPATH to index and mcp. All TokenReview/model mock servers
used loopback port 0 in-process and were shut down and joined. The template test
modifies a copy of the canonical fixture to enable the module; original fixture
values are preserved. kubeconform was unavailable, so its optional strict pass
was skipped; PyYAML parsing and object assertions passed. Docker was available:
runtime and mutation checks executed. A timeout regression also proves candidate
container removal. Production JavaScript syntax checks and Python compilation passed.

## Sanitization

`python <CODEX>/sanitize_selfcheck.py modules/factory`:
**0 fail, 0 warn**. No WARN exceptions are retained.

The final repository/scope and stale-lane grep found **0 matches**. UTF-8/LF and
trailing-newline checks found **0 failures**. No private source snapshot,
node_modules, bytecode, pytest cache, excluded estate corpus or result artifacts
remain in the export.

## Needs from other tasks

- Hardening creates NS_FACTORY and its baseline policies; cluster configuration
  supplies the factory role label, VM runtime and storage class.
- The configuration renderer publishes org-directory in NS_FACTORY, loads module
  defaults/secret declarations, automatically gates the module and invokes the
  deterministic render plugin.
- Sessions install and copy index/MCP into /opt/factory/venv and /opt/factory,
  mount the MCP token, provide FACTORY_API_URL when the module is enabled and
  permit session egress to factory-api:8080. The canonical registry currently
  supplies snapshot/index variables but omits FACTORY_API_URL.
- CI builds the root-context factory Dockerfile and installs locked Python/npm
  test dependencies before the full production verifier suites.
- Model lane deployments expose authenticated lane-gpu-a/lane-gpu-b Services and
  matching app labels on port 8000; secret delivery supplies model-server-auth
  with api-key in the factory namespace and registry pull credentials.
- Monitoring selects the factory ServiceMonitor/PrometheusRule and obtains any
  trusted physical power-state and applied-acknowledgement adapter needed by the
  corresponding optional metrics.
- The workstation-lane task supplies its authenticated operator/device transport
  and enforces team-lead identity for compatibility queue ingestion.

## VERIFY / live steps

No cluster, vendor or external model operation was performed. Platform admins must
verify pod and portal TokenReview behavior, namespace identity labels, secret
projection, actual lane/node mapping, authenticated lane access and CNI policy
behavior both before and after API-server DNAT. Validate the built image, persistent
recovery, observability target selection and snapshot provisioning before enabling
production submissions. Run representative benchmarks on authorised organisation
code before promoting any lane.

## Possible secrets seen

None. Read deployment material contained secret references, not values.

## Open problems

There are no known offline test failures. Production test_gen remains intentionally
NO-GO behind its flag; toy executable checks cannot promote a production Jest lane.
Power state and destination application require trusted observations and explicit
acknowledgements; absent observations are not fabricated. Live integration steps
and the cross-task inputs listed above remain with the responsible tasks.

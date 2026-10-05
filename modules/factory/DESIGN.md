# Factory design

The optional factory converts an approved template and immutable repository snapshot
into bounded work units. A frontier session authors the specification interactively;
local model lanes generate candidates without tools. The service never starts a
frontier session and never uses an interactive seat for escalation.

## Governing rules

A session login remains inside its session container. Factory identity comes from
TokenReview and org-directory, never from card fields. The submitting team must
own the selected estate. A team lead approves a batch before any generation.
Approval freezes a bounded copy and SHA-256 file manifest; workers refuse changed
copies. Cancellation prevents further retries, and current requests have bounded
timeouts. All service processes run in the foreground under pod supervision.
No agent or operator starts detached processes.

Cards are explicit references to code, not executable instructions. Snapshots and
model output are untrusted data. Inputs cannot supply endpoint credentials, host
paths or gate configuration. Repository code never receives model-server secrets.
The engine retains outputs and gate decisions; service audit records exclude
prompts, tokens and file contents. Restricted work requires an explicitly local
lane. Optional API escalation obtains accounts only through pace POST /v1/route;
restricted work never escalates and seats are rejected.

## Card and template schema

Schemas are versioned JSON files in engine/schemas. A card has card=1, unit_id,
task_id, repo, kind, difficulty, target.file and target.symbols. Optional fields
provide instructions, context references, a profile, dependencies and retry budget.
The packer pins target bytes and counts prompt plus reserved output against lane
context minus the safety margin. Oversized or invalid units are rejected before
generation. Each card supports at most three generations; infrastructure failures
consume bounded retries without pretending to be model quality failures.

A template has template=1, template_id, kind, expand and card defaults. Expansion
is limited by its cap and explicit repo roots; the inline profile describes the
language, source roots and verifier. doc_map emits structured documentation for
existing symbols. test_gen remains behind FACTORY_FLAGS=test_gen and is NO-GO
for production until the promotion bench passes on the adopting organisation's code.

## Gates and integration

Generation must satisfy the exact output envelope. Truncated, fenced, empty and
multiple-envelope responses fail. Gate stages parse candidate data, constrain
allowed target edits, splice in an isolated verification copy, and check the
result. doc_map changes documentation while executable source remains unchanged;
JSDoc shape and type checks reject invented or incomplete contracts. test_gen
executes only inside its isolated configured gate runner and must meet syntax,
assertion, mocking, coverage and mutation requirements.

Unit acceptance is provisional until assembly. After a task drains, accepted
units are assembled together, integration checks run, and a patch bundle is built.
An integration failure returns a quarantined result. Destination application is
an explicit acknowledgement and is not inferred from generating or copying a patch.
The factory never pushes repository changes automatically.

## Fair share and approvals

The foreground service collects ready units across every approved task. Each lane
uses weighted deficit round robin, with weight from org-directory teams.json and
one unit costing one credit. A team receives its quantum on a visit and spends it
before the cursor advances. Idle teams retain no accumulated advantage. Within a
team, priority is capped at factory.queue_priority_ceiling, then sequence wins.
The service and shared lane admission enforce factory.max_inflight_per_lane.
Restricted data is excluded from nonlocal lanes before admission.

A continuously eligible team is served within starvation_dispatches dispatches.
The guard charges service to the team's next quantum. N must be at least the
number of configured teams. Team caps, unsatisfied dependencies, retry delays and
data-class restrictions make work temporarily ineligible; impossible capacity
cannot create a dispatch guarantee. The 3:2 fairness test converges to 60/40 over
1000 dispatches and separately tests guard behavior under extreme weights.

The durable queue uses SQLite WAL, synchronous FULL and atomic approval/state
transactions. Every lead decision records the actor subject or slug and actor_kind.
Claim identifiers fence stale completions after crash recovery. Expired worker
leases may be reclaimed; cancellation cannot be overwritten by a late completion.
The HTTP service uses the same queue as the worker. Workstation transfer commands
are documented in queue/README.md and do not enable autonomous snapshot feeding.

## Metrics contract

One authoritative exporter serves /metrics for each lane/node queue partition.
Labels are bounded: lane, node, kind and outcome/event; IDs, paths, prompts,
error messages and snapshot hashes never become metric labels. Scrape every 30 s.
A successful coherent store refresh advances snapshot_timestamp_seconds; stale
or missing observations are unknown, not an empty queue.

| Metric | Type and labels | Meaning |
| --- | --- | --- |
| aa_factory_queue_depth | Gauge: lane,node | Approved nonterminal units, including waits. |
| aa_factory_ready | Gauge: lane,node | Eligible units with satisfied dependencies and elapsed retry delay. |
| aa_factory_inflight | Gauge: lane,node | Units holding a dispatch reservation through verification. |
| aa_factory_snapshot_timestamp_seconds | Gauge: lane,node | Last successful coherent refresh; freshness limit 120 s. |
| aa_factory_attempts_total | Counter: lane,node,kind,outcome | pass, fail_parse, fail_apply, fail_verify, fail_runtime, cancelled. |
| aa_factory_units_total | Counter: lane,node,kind,event | accepted, bounced, applied; acknowledgement required for applied. |
| aa_factory_gpu_seconds_total | Counter: lane,node | Union of generation occupancy intervals per physical lane; excludes waits and verification. |
| aa_factory_node_suspended | Gauge: lane,node | Confirmed suspended=1 or awake=0; omit unknown power state. |

Counters derive from durable idempotent events, including crash/resume/replay.
Each attempt has one outcome; accepted and bounced are terminal alternatives.
Supported partitions are zero initialized, while missing partitions remain unknown.
Queue invariants are queue_depth >= ready + inflight >= 0. GPU occupancy uses
bounded actual generation request intervals, including failed requests; overlapping
concurrent slots count once. Configured lane/node mapping must match physical
placement; token throughput or request latency alone does not prove delivered value.

Idle alerts require fresh positive demand, zero inflight and generation occupancy,
a confirmed awake node and healthy model telemetry. Suspended demand requires
fresh explicit power observations. Parse/apply failure alerts require more than
30 percent of at least ten attempts over an hour. Missing inputs suppress these
alerts. Exporter availability alerts remain necessary.

## Metrics contracts

The factory module owns factory alert rules. Queue metrics are `aa_factory_queue_ready{team,lane}`, `aa_factory_dispatch_total{team,lane,outcome}`, and `aa_factory_oldest_ready_age_seconds{team,lane}`. Lane Service names equal registry names verbatim and use port 8080 with `/v1`; the rendered lanes ConfigMap comes from the GPU lane model.

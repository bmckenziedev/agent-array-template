# Factory engine

The engine packs versioned cards against immutable snapshots, generates candidates
on local model lanes, gates each candidate and builds an integration-checked bundle.

## Interface

`python factory.py submit --snapshot <path> --template <file> --no-start` persists
work. The pod foreground service dispatches across all tasks; CLI submission never
spawns a process. `worker` is an explicit foreground development command.
Schemas live in schemas; JavaScript gate tooling lives in js. The old store is
migrated additively to schema 2; unattributed legacy work must be assigned through
an approved batch before team dispatch.

## Configuration

`FACTORY_HOME` selects state. `FACTORY_FLAGS=test_gen` is experimental and NO-GO
until the org-code bench gate passes. `--lanes` accepts a lanes.v1 overlay; examples
are lane-gpu-a and lane-gpu-b. Each lane config has endpoint, concurrency, context,
prompt cap, explicit local flag and an optional trusted physical node mapping.
Team weights, priority ceilings and per-lane caps come from org-directory teams.json.
The module defaults define queue bounds, starvation N and worker polling.

## Secrets

Model endpoint keys are read at request time through trusted lane key_file/key_env
configuration. User cards cannot provide them. See ../secrets.required.yaml.

## Deploy

The optional module deployment supervises the authenticated API and foreground
engine loop. The factory image installs pinned npm tools. Docker verification runs
one foreground isolated container per request and removes it on completion/timeout.
There is no workstation feeder or automatic frontier invocation.

## Verify

`python -m unittest discover -s modules/factory/engine/tests -t modules/factory/engine`
runs the offline scheduler, schema and store proofs. Docker-only gates report a
skip if the Docker engine is unavailable. The benchmark is required before promotion.

## Rollback

Stop dispatch and retain the PVC before reverting the image. Schema 2 adds columns
without discarding existing output; older images must not open newer stores.

## Security notes

Restricted work requires local=true. Team caps apply per lane across tasks. Candidate
code runs only in isolated verification; the local doc_map runner parses and type
checks without executing model output. Integration assembly precedes result delivery.

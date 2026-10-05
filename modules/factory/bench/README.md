# Factory benchmark framework

The benchmark gates use a newly written toy package with three documentation units and two test-generation units. No external estate corpus is included. Run `python modules/factory/bench/bench.py selftest` to verify planted bad output rejection. Executable candidates run only in bounded, offline Docker containers; absent Docker produces an explicit skip. No model servers are started by this framework.

The three documentation units use the production `{symbol: JSDoc}` artifact and the engine's exact `js/gate.js` verifier: JSON scope and hygiene, comment-only token preservation, parameter/return coverage, type-strength floor and TypeScript diagnostic delta. This parser/typechecker path does not execute repository or candidate code. It requires Node and the engine's locked JS dependencies (`npm ci --ignore-scripts` in `engine/js`). The two executable test units exercise a small Node-assert mutation adapter; they are teaching checks and cannot promote production Jest test-generation lanes. Production `test_gen` stays NO-GO pending the representative production verifier benchmark.

Build an org profile by taking an immutable, authorised repository snapshot, selecting small deterministic exported functions, recording expected exports and mutation sites, and writing independent reference outputs. Validate those references and plant malformed, ungrounded and ineffective outputs before measuring a lane. Record model identity, decoding settings, attempt outcomes, pass rate, mutation score, runtime and resource usage alongside the snapshot revision. Candidate code must never execute on the host.

Never promote a lane without a bench run on the organisation's own code. The toy package verifies the harness, not production suitability. `test_gen` remains NO-GO behind its engine flag until a representative org benchmark meets the approved accuracy and cost thresholds. Promotion requires explicit team-lead review of results, rejected examples and the workload's data class.

The `gate UNIT.json CANDIDATE` command returns zero only on a full pass; a skipped executable gate never counts as a promotion pass. `models.json` illustrates generic local endpoints. Images must be pinned to reviewed digests for a promotion run.

Run `profile SNAPSHOT REVIEWED-UNITS.json PROFILE.json` to bind org-authored units to a repository snapshot and source SHA-256 hashes. `run PROFILE.json MODEL.json RESULTS.jsonl` records gated outcomes for an OpenAI-compatible endpoint; `--token-file` reads its credential without putting it in arguments. Paths must remain within the snapshot, and changed source hashes fail the snapshot gate. Multi-module fixtures require self-contained target modules or a separately reviewed gate adapter.

## Interface

The CLI exposes `selftest`, `profile`, `gate` and `run`. Profiles contain immutable source hashes and reviewed unit definitions; results are bounded JSONL outcome records.

## Configuration

Each model configuration supplies an endpoint, model ID and output-token limit. The gate image is digest-pinned. Unit `repo_root` selects an authorised snapshot; synthetic units default to the bundled toy repository.

## Secrets

Endpoint credentials are read from an explicit token file. Profiles and results contain no credential values.

## Deploy

Run the harness from a reviewed checkout with local Docker. It is an offline promotion framework rather than a deployed service.

## Verify

Run `python -m unittest discover -s modules/factory/bench/tests -t modules/factory/bench` and the CLI selftest. Docker availability is required for executable promotion evidence.

## Rollback

Retain prior profiles and results, restore the previous lane selection, and rerun the representative org benchmark before promoting another model.

## Security notes

Model output executes only within bounded Docker containers without network access. Source paths cannot escape the selected snapshot. Changed source hashes invalidate the profile.

Self-test summaries report unavailable production tooling under `skipped` and
`skip_reasons`; skipped gates never contribute to `passed`. Install Node 22 and
run `npm ci --ignore-scripts --prefix modules/factory/engine/js` to exercise
the documentation gates. Executable candidate gates additionally require Docker
with a reachable daemon and the pinned gate image. They retain an unprivileged
UID, read-only filesystem, and disabled network.

# Offline CI tools

Discover component tests and validate rendered manifests, admission expressions and documentation.

## Interface

`python tools/ci/run_tests.py [--list] [--only GLOB]` discovers each component's
`tests/test_*.py` and `tests/test-*.sh`, excluding live tests, fixtures and environments.
Python suites use pytest when available, otherwise unittest. Dependencies declared next to
`tests/` are installed into temporary isolated virtual environments that are removed
after each suite. CI may install packages; suite code remains offline.
Bash absence is reported as SKIP. Installation and suite failures fail the run.

`bash tools/ci/validate_manifests.sh RENDERED VERSION` filters Kubernetes YAML documents
and applies strict kubeconform schemas, including the CRD catalog. Missing CRD schemas
are ignored. Client kubectl dry-run is a weaker fallback when kubeconform is absent.
`python tools/ci/lint_cel.py RENDERED` checks optional-field guards in admission policies.
This is a conservative textual lint, not a CEL type checker or proof of control-flow safety.
Presence checks must use the same path; referenced variables can supply a presence guard.
`bash tools/ci/promtool_tests.sh RENDERED` extracts alert rules and rewrites test rule paths.
`python tools/ci/linkcheck.py --root .` checks relative links and heading anchors offline.
`pwsh -File tools/ci/check_powershell.ps1` parses PowerShell syntax.
See [Action pinning](PINNING.md) for release checksum and action adoption steps.

## Configuration

No organisation placeholder keys are declared. CLI paths select inputs.

## Secrets

No secret values or secret references are required.

## Deploy

Run the tools locally or through the hosted GitHub workflows. No cluster deployment is required.

## Verify

Run `python -m unittest discover -s tools/ci/tests -t tools/ci -v`.
Commands exit 1 for findings or failures and 2 for invalid arguments.

## Rollback

Revert tool or policy changes and rerun the offline checks.

## Security notes

Offline checks do not replace positive and negative admission server dry-runs.
External documentation URLs are never fetched. Tool installation refuses unverified archives.

`check_contracts.py --rendered <directory> --root .` checks Argo path/project ownership,
ServiceMonitor named ports, CoreDNS peer selectors, proxy resourceNames, session ConfigMap
and image producers, required Dockerfiles and application group naming. It needs PyYAML
and exits nonzero on any seam. CI runs it on both module configurations; Make validate
runs it on the local render. Third-party suites run from component roots in temporary
venvs; hashed production requirements and test requirements install separately. Local
network installation failures are explicit skips; CI treats them as failures.

The unit workflow installs Node 22 and the engine's pinned JavaScript lockfile using
`npm ci --ignore-scripts`. Benchmark prerequisites that are absent locally produce
explicit skips; benchmark self-tests count skips separately from passes.

`module_config.py --out <temporary-directory>` creates the all-module CI fixture.
It preserves the strict YAML subset and supplies synthetic GPU model pins on example
nodes. `--strict-fixture` also supplies synthetic image pins and explicit session nodes
to exercise strict rendering offline. Neither fixture is deployment evidence; never
deploy its synthetic pins. Unverified deployment artifacts remain blocked by strict render.

Native kubeconfig/API audit configuration and Wazuh merge/delete fragments remain
under `files/`. The exact path/shape allowlist in `rendered_assets.py` validates them
before excluding them from Kubernetes resource validation; unknown resources still fail.
Velero ConfigMaps and sealed-secrets metrics resources are ordinary GitOps manifests.
Promtool tests substitute the selected org's metric names and thresholds before running;
`promtool_tests.sh RENDERED [ORG_CONFIG]` selects the org explicitly when needed.

The image workflow builds all nine Dockerfiles without publishing by default. Publishing
requires repository variables `PUBLISH_IMAGES=true`, `REGISTRY` and `IMAGE_PREFIX`, plus
registry package access for the workflow identity. Missing configuration produces a clear
publishing skip. The template never assumes it may write an existing package namespace.

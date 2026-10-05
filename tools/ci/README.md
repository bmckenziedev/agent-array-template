# Offline CI tools

Discover component tests and validate rendered manifests, admission expressions and documentation.

## Interface

`python tools/ci/run_tests.py [--list] [--only GLOB]` discovers each component's
`tests/test_*.py` and `tests/test-*.sh`, excluding live tests, fixtures and environments.
Python suites use pytest when available, otherwise unittest. Dependencies declared next to
`tests/` are installed into `.ci-venvs/<component>`. CI may install packages; suite code remains offline.
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

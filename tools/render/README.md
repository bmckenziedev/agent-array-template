# Org renderer

Turn the reviewed org configuration into deterministic committed manifests and supporting files.
Python 3.10+ and the standard library are the runtime interface; no cluster access is required.

## Interface

Run `python tools/render/render.py --org org/org.yaml --out rendered` from the repository root.
`--root` selects a synthetic or alternate repository. `--strict` refuses every model warning.
`--check` reports drift without changing the output. `--validate-only` checks configuration;
`--print-model` prints stable JSON. `--list-keys`, optionally `--scope user-tool --markdown`,
lists placeholders. `--lint-placeholders PATH...` checks template scope and documentation tokens.
Exit codes are 0 for success, 1 for drift or placeholder lint, and 2 for configuration or usage errors.

## Configuration

The [org reference](../../org/README.md) documents the files, YAML subset and complete placeholder list.
Core components declare `scope: components`, a `name`, and a `defaults` mapping in
`org.component.defaults.yaml`; optional modules use `scope: modules`. Overrides deep merge,
with lists and scalars replaced. These settings become `C_<COMPONENT>_<KEY>` or `M_<MODULE>_<KEY>`;
lists append `_JSON`.

## Secrets

The renderer handles secret names and key names only. Values are provisioned separately; the generated
secret index lists enabled components' requirements.

## Deploy

Copy the example org files and MCP examples, edit the file pointers and settings, then run `make render`.
Commit `rendered/`; Argo CD syncs its manifest subtrees. The output must be empty or contain
`.rendered-by-aa-render`. Stale files are removed only inside that marked directory.
Templates and plugins are trusted repository code and must be reviewed before rendering.
Plugins are stdlib-only and must avoid network and external I/O; emitted paths are enforced.

## Verify

Run `python -m unittest discover -s tools/render/tests -t tools/render -v` and `make check`.
Install `requirements-dev.txt` into an isolated development environment for optional parser parity
and JSON Schema checks. Runtime rendering never imports those packages.

## Rollback

Restore the reviewed org inputs and `rendered/` from the previous Git revision, render and check,
then let Argo CD sync that revision. Rendering does not provision or revoke credentials.

## Security notes

Only marked or empty output directories may be replaced. Plugin emit paths reject traversal and
absolute paths, and remain inside the plugin directory. Review plugins as trusted executable code.

Defaults always deep-merge into `model.org.components` or `model.org.modules` before gating, including disabled modules. Missing RENDER-IF paths are false; equality compares string representations. Disabled plugins are never imported. Duplicate output paths fail rendering. Registry `files:` paths resolve against `--root`. Placeholder lint skips tests, fixtures and docs; non-template lint covers only YAML, JSON, env, shell and TOML in k8s/helm directories.

Argo source trees users/, mcp/ and global/ reject kustomization.yaml, Chart.yaml and values
files; Helm inputs belong in files/. Strict rendering also rejects all-zero C_*_IMAGE
digests. Optional user git blocks reference Secrets by name only and normalise into
users.json; only the CLI receives the read-only credential mount.

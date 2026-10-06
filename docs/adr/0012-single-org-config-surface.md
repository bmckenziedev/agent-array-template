# 0012: Single organisation configuration surface

Status: Accepted (design decision D2).

## Context

Many export tasks built components in parallel. Each needed the same organisation facts (users, teams, accounts, namespaces, images, policy) without depending on files another task was still writing. Adopters also need one place to describe their deployment.

## Decision

Use `org/*.yaml` as the only configuration surface. Files use a strict YAML subset parsed by the standard-library renderer, with no third-party runtime dependency. The canonical examples and the normalised fixture form the cross-component contract: components consume exact uppercase `{{KEY}}` placeholders from the published key list and component `C_`/`M_` defaults, and unknown keys fail.

## Consequences

Components stay consistent without importing each other's code, and every key is discoverable with `render.py --list-keys`. The YAML subset excludes anchors, tags, multiline scalars, floats and nested flow collections, so some idiomatic YAML is refused. Adding a setting means updating schema, normaliser, key list, reference documentation and tests together.

Integration refinement: the fixture lives at `tools/render/tests/fixtures/org.fixture.json`, with MCP/context registry copies under `tools/render/tests/fixtures/mcp/`; the normaliser produces that canonical shape and `--print-model` exposes it for review. Integration also fixed renderer semantics: defaults deep-merge before gating, missing `RENDER-IF` paths are false, equality compares string forms, disabled plugins are never imported, duplicate output paths fail, registry paths resolve against `--root`, and `username_claim` must be `sub`.

## Alternatives considered

- Helm values or Kustomize overlays per component: rejected because they scatter organisation facts and do not validate cross-file references.
- A full YAML parser dependency: rejected to keep rendering stdlib-only, deterministic and reviewable.
- Per-task private configuration files: rejected because parallel tasks would drift.

## What would make us revisit it

A configuration need the subset cannot express safely, or a renderer replacement that keeps determinism, strict key checking and no-network plugins.

## Related files

- [org/README.md](../../org/README.md)
- [org/org.example.yaml](../../org/org.example.yaml)
- [tools/render/README.md](../../tools/render/README.md)
- [tools/render/aa_render/yamlsub.py](../../tools/render/aa_render/yamlsub.py)
- [tools/render/aa_render/key_names.py](../../tools/render/aa_render/key_names.py)
- [tools/render/tests/fixtures/org.fixture.json](../../tools/render/tests/fixtures/org.fixture.json)

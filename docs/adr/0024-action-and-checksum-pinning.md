# 0024: Action and tool checksum pinning

Status: Accepted (design decision D14).

## Context

Workflow actions and downloaded tools should be pinned to verified artifacts. The export was produced offline by automated agents, which cannot look up commit SHAs or checksums and must not invent them.

## Decision

Actions are pinned by major tag, Dependabot is enabled, and a PINNING note tells the organisation to pin full commit SHAs. Tool checksums are left as `REPLACE_WITH_SHA256` when they cannot be verified offline.

## Consequences

No fabricated SHA or checksum enters the tree. Major tags are mutable, so adopters must resolve and verify each tag before relying on CI. Placeholder checksums intentionally block installation until replaced.

Integration refinement: kubeconform v0.6.7 and Prometheus v3.2.1 checksums in `install_tools.sh` were filled from upstream release checksum assets; the placeholder guard remains for future updates. Dependabot tracks GitHub Actions and pip weekly. Strict rendering separately refuses all-zero image digests, and images publish only with explicit registry configuration.

## Alternatives considered

- Pin SHAs during export: rejected because they could not be verified offline.
- Unpinned or floating tags without guidance: rejected because they leave supply-chain risk undocumented.

## What would make us revisit it

An export process with verified network access to resolve and attest SHAs, or an organisation policy requiring SHA pins before first CI run.

## Related files

- [tools/ci/PINNING.md](../../tools/ci/PINNING.md)
- [tools/ci/install_tools.sh](../../tools/ci/install_tools.sh)
- [tools/ci/README.md](../../tools/ci/README.md)
- [.github/dependabot.yml](../../.github/dependabot.yml)
- [.github/workflows/ci.yml](../../.github/workflows/ci.yml)

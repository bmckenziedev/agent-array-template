# 0011: Fresh export tree without source history

Status: Accepted (design decision D1).

## Context

The template is derived from a private source deployment. That repository's history carries personal identity (authors, committers, paths and topology), so no part of it may be published. The source working tree was also being edited concurrently by other work, so it was not a stable input for a multi-task export.

## Decision

Build the template as a fresh tree with no source history. Export tasks read one pinned source commit through `git archive`, never a live working tree. The orchestrator initialises the repository and makes a single commit only after every task and gate passes. Source content enters the tree only as reviewed, rewritten work that passes the [sanitization gate](../../tools/sanitize/README.md).

## Consequences

The public history starts at the initial export recorded in the [changelog](../../CHANGELOG.md); provenance of individual lines is not traceable to the source. Every task works from the same immutable input, so results are reproducible and concurrent source edits cannot leak in. Later changes follow ordinary review through [contributing](../../CONTRIBUTING.md); no source history, live reports or personal identifiers may be added afterwards.

Integration refinement: the handover sanitizer additionally runs with `--export-gate`, which forbids sealed ciphertext in the exported tree; adoption CI omits that flag (see [0022](0022-hashed-sanitization-inventory.md)). Integration recorded that no source-repository files were read or copied, and no source-repository mutation, commit or push was performed.

## Alternatives considered

- Filtering or rewriting source history: rejected because identity also appears in content, messages and paths, and a missed rewrite is irreversible once published.
- Reading the live source working tree: rejected because concurrent edits make task inputs inconsistent and unreviewable.
- Squashing the source repository: rejected because it still starts from material that was never sanitized as a whole.

## What would make us revisit it

A need to publish provenance for specific components, or a source repository whose full history has been independently reviewed and cleared for publication.

## Related files

- [CHANGELOG.md](../../CHANGELOG.md)
- [CONTRIBUTING.md](../../CONTRIBUTING.md)
- [tools/sanitize/README.md](../../tools/sanitize/README.md)
- [docs/INTEGRATION.md](../INTEGRATION.md)

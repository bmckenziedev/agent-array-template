# Optional session-jobs integration patches

Committed unified patches are derived from minimal neutral fixtures in `tests/fixtures/session-jobs`. They demonstrate three coupled changes: exact pod-level Localhost profile, guarded admission rejecting container overrides, and installer refusal when the node lacks the profile.

The generator reads a caller-specified `--target` directory and writes patch files only inside this module. Missing or repeated anchors fail closed. It never mutates the target. Regenerate against the actual session-jobs files, review the diff and coordinate the template plus admission change together. These patches are not applied by default or by the renderer.

`python modules/seccomp-gvisor/patches/make-patches.py --check` checks fixture freshness; `tests/check_admission_patch.py --offline` additionally proves application in an isolated temporary directory.

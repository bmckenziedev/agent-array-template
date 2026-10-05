# Offline and live verification

`test_profile.py` exercises reference strictness and clone3/ptrace/listener safety. `test_patches.py` checks idempotent transformations and fail-closed anchors. `check_admission_patch.py --offline` applies committed patches to owned fixtures in a temporary directory and parses the results.

`live/` contains probe and sweep payloads for dedicated, credential-free diagnostic pods. These tools are never collected by offline suites. Runtime behavior and server admission need a platform-admin supplied kubeconfig and an explicitly reviewed diagnostic namespace.

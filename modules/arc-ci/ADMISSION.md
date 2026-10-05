# Admission verification
Run `ARC_RENDERED_DIR=RENDERED_FILES_DIR python tests/live/check_admission.py --kubeconfig OIDC_CONFIG`.
The suite includes plain busybox:1.36 Pod and Deployment outside the selected runner namespaces,
minimal sandbox pod with absent optional fields, the rendered light/heavy templates and negative
hostPath, secret, privilege, runtime override, service-account and ephemeral-container cases.
Policy-scoped denial messages are checked separately from schema, RBAC and PSA refusals.
Only use `--live` during a window; temporary objects are cleaned by the suite.
Enable the binding only after positive and negative dry-runs pass on the target Kubernetes version.

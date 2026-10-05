# Node contract

Rendered node data expands org roles and runtime classes into additive owned labels and the GPU NoSchedule taint.

## Interface

`apply-node-contract.py --rendered <dir> [--yes]` reads nodes/*/cluster/node-contract/labels.json, validates node/role/runtime identifiers, prints shell-quoted commands and executes only with --yes. Exit 0 means success; argparse input failures exit 2; kubectl failures propagate.

## Configuration

Uses NODE_NAME, NODE_ROLES_JSON, NODE_RUNTIME_CLASSES_JSON, NODE_LABELS_JSON, NODE_IS_GPU and LABEL_PREFIX. Role/runtime prefixes and the conditional GPU taint are explicit in the template. Unowned custom labels are ignored. Role/runtime labels are derived from the entity arrays.

## Secrets

No secrets. Execution consumes the platform admin's current OIDC kubeconfig.

## Deploy

Render, inspect the commands and use --yes after node runtime verification. Never label a runtime as available before its handler is tested.

## Verify

`python -m unittest discover -s cluster/tests -t cluster` uses a fake kubectl on PATH. Repeat execution is additive and uses --overwrite.

## Rollback

Review/drain then manually remove stale owned labels/taints; automatic removal is deliberately absent.

## Security notes

No shell evaluation or removal of unrelated labels occurs. Node labels are admin-controlled placement assertions, not identity boundaries.

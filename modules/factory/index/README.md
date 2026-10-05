# Factory index

Optional factory component: static snapshot indexing and dependency analysis.

## Interface

Run `python -m estate_index --help`. Package mapping comes from package.json; REPOS.md comparison is optional.

## Configuration

FACTORY_PACKAGE_SCOPE defaults to @example-org/. FACTORY_SNAP_ROOT and FACTORY_INDEX_ROOT select snapshot and index directories. FACTORY_COMPARE_REPOS_MD optionally compares a generic map. FACTORY_API_URL enables API submission and status.

## Secrets

MCP reads the projected token at /var/run/agent-array/mcp-token/token. Identity comes only from that token.

## Deploy

Session images copy index and MCP to /opt/factory and install the hash-locked dependencies into /opt/factory/venv.

## Verify

Install requirements.txt and requirements-test.txt in a temporary virtual environment with --require-hashes; run pytest tests.

## Rollback

Restore the previous session image; indexes can be rebuilt from snapshots.

## Security notes

Repository content is untrusted data. No repository code executes. Symlinks and paths outside the snapshot are refused. Responses are bounded. Submission requires API authorization and team-lead approval. test_gen remains NO-GO until the bench gate passes.

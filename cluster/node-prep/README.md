# Login node preparation

The host prep template checks encryption ancestry and plans a root-owned 0711 login parent directory.

## Interface

Rendered prepare-login-root.sh is read-only by default; --require-encrypted refuses unencrypted ancestry and --yes changes the directory. Exit 2 indicates an unknown option; refusal exits 1.

## Configuration

LOGIN_HOST_ROOT sets the parent. AA_FAKE_ROOT prefixes that path for isolated tests. findmnt and lsblk must be available; device ancestry must include crypt. This verifies dm-crypt mapping, not key custody or remote cloud encryption attestation.

## Secrets

Encryption keys are provisioned outside this component.

## Deploy

Run --require-encrypted, inspect the plan, then --require-encrypted --yes as root before provisioning logins.

## Verify

`bash cluster/tests/test-prepare-login-root.sh` stubs device tools and exercises encrypted and refusal paths.

## Rollback

Remove an empty parent only after confirming no PVs/logins refer to it. Never recursively delete the parent for rollback.

## Security notes

0711 permits traversal without directory listing. A symlink at the target is refused; stage under a root-controlled parent. AA_FAKE_ROOT is a test mechanism and must be unset in production.

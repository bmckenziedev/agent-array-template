# Sanitization gate

Reject identifying text, credentials and forbidden artifacts before publication.

## Interface

`python tools/sanitize/scan.py --root . [--warn-as-fail] [--json]`.
Structural rules detect personal home paths, personal email domains, PEM bodies,
credential-shaped tokens, ciphertext, home-network hosts and non-example access hosts.
Hash checks cover exact tokens, punctuation sub-tokens, and two/three-token phrases.
Forbidden artifacts fail independently of text scanning; binary detection uses the first 4 KiB.
Binary documentation PNG/SVG assets are exempt; text SVG remains scanned.
Allow patterns are repository relative; fixtures and the hash inventory are exempt.

Platform admins add identifiers using an external policy/inventory and run
`python tools/sanitize/build_denylist.py --identifiers INPUT.json --policy POLICY.json --out tools/sanitize/denylist.json --extra-atom TEXT`.
Use `--new-salt` to rotate the salt. Keep cleartext inputs outside the repository.
Only the hash output is committed. Avoid putting identifying arguments in shared shell history.
Labels are neutral kinds and numbers; no matched text is printed.

## Configuration

No organisation placeholder keys are declared. CLI paths select inputs.

## Secrets

No secret values or secret references are required.

## Deploy

Run the tools locally or through the hosted GitHub workflows. No cluster deployment is required.

## Verify

Run `python -m unittest discover -s tools/sanitize/tests -t tools/sanitize -v`.
Commands exit 1 for findings or failures and 2 for invalid arguments.

## Rollback

Revert tool or policy changes and rerun the offline checks.

## Security notes

HMAC hashes do not make the inventory secret: the salt is public and short atoms are
brute-forceable. This gate prevents accidental export, not adversarial recovery or every
possible spelling variant. Never commit the original identifier inventory.

Handover runs `scan.py --root . --export-gate` to forbid sealed ciphertext. Adoption CI omits this flag so reviewed organisation SealedSecrets remain valid inputs.

# 0022: Hashed sanitization inventory

Status: Accepted (design decision D12).

## Context

The organisation's CI must fail if any personal or organisational identifier leaks into the public tree. A cleartext denylist would itself publish those identifiers.

## Decision

The sanitization gate ships HMAC-salted hashes of identifier atoms plus clear structural rules. The cleartext identifier list never enters the export.

## Consequences

CI can reject known identifiers without publishing them. The salt is public and short atoms are brute-forceable, so this is hygiene against accidental export, not secrecy. Platform administrators maintain the inventory outside the repository and regenerate hashes with `build_denylist.py`; findings print neutral kinds and numbers, never matched text.

Integration refinement: the prohibition on sealed ciphertext applies only with `--export-gate` at handover; adoption CI omits the flag so reviewed organisation SealedSecrets remain valid inputs. The link checker reuses the same hash inventory to reject original-source URLs. Rules were not weakened to pass; remaining warnings are synthetic test dates.

## Alternatives considered

- A cleartext denylist: rejected because it publishes the identifiers.
- Structural rules only: rejected because they cannot recognise specific names.
- Manual review only: rejected because it is not a repeatable CI gate.

## What would make us revisit it

A requirement for secrecy of the inventory (which would need a private CI step), or a leak that the atom and phrase hashing missed.

## Related files

- [tools/sanitize/README.md](../../tools/sanitize/README.md)
- [tools/sanitize/scan.py](../../tools/sanitize/scan.py)
- [tools/sanitize/build_denylist.py](../../tools/sanitize/build_denylist.py)
- [tools/sanitize/denylist.json](../../tools/sanitize/denylist.json)
- [tools/ci/linkcheck.py](../../tools/ci/linkcheck.py)

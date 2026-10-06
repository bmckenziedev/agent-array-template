# 0025: Apache-2.0 licence as a placeholder

Status: Accepted (design decision D15).

## Context

A public template needs a licence file, but the choice belongs to the copyright holder and the adopting organisation, not to the export process.

## Decision

LICENSE ships the Apache-2.0 text as a suggestion. The README states that the copyright holder and the adopting organisation must confirm the licence.

## Consequences

The tree is usable as an Apache-2.0 template, and the confirmation step is explicit before publishing. NOTICE and disclosure placeholders must be completed by the adopter.

## Alternatives considered

- No licence file: rejected because it leaves reuse rights unclear.
- Choosing a licence on the holder's behalf: rejected because that decision is not the export's to make.

## What would make us revisit it

The copyright holder or adopting organisation choosing a different licence.

## Related files

- [LICENSE](../../LICENSE)
- [NOTICE](../../NOTICE)
- [README.md](../../README.md)

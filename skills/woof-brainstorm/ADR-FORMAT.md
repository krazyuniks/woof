<!-- VENDORED from agent-toolkit skills/brainstorm - do not edit here. Regenerate with `just gen-brainstorm`. -->

# ADR format

Architecture Decision Records live in `docs/adr/`, named `NNNN-slug.md` and numbered
sequentially. Create the directory only when the first ADR is needed; the next number is one above
the highest already present.

## When to write one

Write an ADR only when all three hold:

1. Hard to reverse: real cost to undo later.
2. Surprising without context: a future reader will question the choice.
3. The result of real trade-offs: genuine alternatives existed and one was chosen.

Skip decisions that are easily reversible, self-evident, or made with no alternative.

## Minimum

- A short title.
- One to three sentences covering the context, the decision, and the rationale.

## Optional, when useful

- Status: `proposed | accepted | deprecated | superseded by ADR-NNNN`.
- Considered options: alternatives worth remembering.
- Consequences: non-obvious downstream effects.

## Good subjects

Architectural shape, integration approaches between components, high-switching-cost technology
choices, ownership and scope boundaries, deliberate deviations from convention, and non-obvious
constraints (compliance, performance).

Adapted from the grill-with-docs skill (`mattpocock/skills`). See `ACKNOWLEDGEMENTS.md`.

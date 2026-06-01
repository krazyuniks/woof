<!-- VENDORED from agent-toolkit skills/brainstorm - do not edit here. Regenerate with `just vendor-brainstorm`. -->

---
name: brainstorm
description: Interactive design discovery before any build. Use when the user wants to design, architect, scope, or think through a feature, system, or change before implementation, or says /brainstorm. Runs two loops (Brainstorm then Grill Me) and produces a schema-validated design bundle (architecture doc + CONTEXT.md glossary + ADRs) for a Woof epic, a BQ backlog, or a standalone design.
---

# Brainstorm

Interactive design discovery. Turn a rough idea into a design the user agrees on, then hand a
schema-validated bundle to whatever builds it. Built on DDD and hexagonal architecture, used as a
lens and never imposed as vocabulary. Full rationale and diagram: `README.md`.

## Hard gate

Do not write code, scaffold a project, or take any implementation action until you have run the
loops below and the user has approved the design. Process comes before implementation: a request
to "build X" triggers this skill first.

## Loop 1 - Brainstorm (generative; the user leads)

Goal: a complete design document the user is happy with.

1. Explore context first. Read the relevant files, docs, and (brownfield) code before asking.
2. Diverge. Research, apply thinking lenses, and generate candidate directions. Do not cap the
   options or the turns.
3. Synthesise the current design into the tiered template (`TEMPLATE.md`). Pick the tier that
   matches the problem; mark irrelevant sections `N/A`.
4. Present it. The user discounts, adds, redirects. Re-synthesise. Loop until the user says the
   design is complete. Depth scales with difficulty, not a turn count.

Elicit with plain questions that carry DDD/hex shape, never the jargon (full table in `README.md`):
distinct parts and their seams, who owns and may change each piece, the exact words and their
negations, what each part needs from outside and what is on the other side, which way dependencies
point, what must always be true, what changes together vs independently.

Capture the user's own terms. Never coin names or apply framework labels prematurely. When two of
the user's words collide, flag it for Loop 2 to resolve; do not rename it yourself.

Output: the design document = Contract 1.

## Loop 2 - Grill Me (critical; you lead)

Goal: shared understanding, no loose ends. Run until resolution:

1. Interview one question at a time; wait for the answer before advancing.
2. Explore the codebase first; answer from code before asking.
3. Challenge terminology against the emerging glossary.
4. Stress-test with concrete scenarios; expose edge and boundary cases.
5. Cross-reference code; surface contradictions between stated behaviour and implementation.
6. Capture resolved terms into `CONTEXT.md` immediately (`CONTEXT-FORMAT.md`).
7. Record an ADR only when the decision is hard to reverse AND surprising without context AND the
   result of real trade-offs (`ADR-FORMAT.md`).

If the design is wrong at the root, stop and return to Loop 1. Exit when the user and you share a
clear, consistent understanding and dependencies are resolved.

Output: the resolved bundle = Contract 2 = design doc + `CONTEXT.md` + ADRs + open questions.

## Contracts

Three schema-validated boundaries (JSON Schema over markdown + YAML front matter), in `schemas/`:

- Contract 0 (`input.schema.json`): spark + chosen tier/template + (brownfield) repo context.
- Contract 1 (`design.schema.json`): the design document handed to Loop 2; the body carries the
  tiered template sections, required per tier or marked `N/A`.
- Contract 2 (`bundle.schema.json`): the resolved bundle. Its front-matter carries the
  decomposition manifest - `work_units[]`, `open_questions[]`, `context_ref`, `adr_refs[]`.

Each `work_unit` is `{id (WU<n>), title, summary, bounded_context, acceptance[], deps[]}` - the
neutral hook every consumer decomposes. Name it in the bundle's ubiquitous language; let the
consumer derive its own fields (Woof story acceptance, BQ `allowed_paths`/`verify`) from it.

Validate an artefact at each boundary with `validate.py input|design|bundle <path>` (a `uv run`
script; `jsonschema` + `pyyaml`). It runs the schema plus the body-section and work-unit-graph
checks the schema cannot express. Woof and BQ validate the same contracts natively in their own
stacks.

## Handoff

- Woof: the bundle seeds `woof brainstorm`, which validates it and feeds the deterministic graph.
- BQ: the bundle's work-units decompose into task files.
- Standalone / KB: the bundle is the deliverable.

## Modes

- SDLC: the full DDD/hex tiered template and a Woof or BQ handoff.
- General / KB: a lighter design or decision document; the DDD slant generalises to pinning the
  precise terms and the scope boundaries, which still yields a glossary and shape for Loop 2.

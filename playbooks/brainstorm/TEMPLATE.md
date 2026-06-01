<!-- VENDORED from agent-toolkit skills/brainstorm - do not edit here. Regenerate with `just vendor-brainstorm`. -->

# Architecture / design template (tiered)

The Loop 1 output. Fill the sections that fit the problem and mark the rest `N/A`. The tier sets
how much engages. Distilled from the GTS `REFERENCE-ARCHITECTURE.md` taxonomy; that file and
`GTS-Technical-Architecture.md` are kept as worked examples, not as this template.

The front-matter is validated by `schemas/design.schema.json` (Loop 1 -> Loop 2) and
`schemas/bundle.schema.json` (the resolved bundle); `validate.py` additionally checks that the
body carries the sections this tier requires.

## Tiers

- `kb-topic` - a light decision or research note. Core sections only, no hexagonal detail.
- `feature` - a change within an existing system. Core sections.
- `subsystem` - a new bounded component. Core plus the relevant extended sections.
- `system` - a whole system. The full taxonomy, optionally with a concrete technical layer.

## Front matter

```yaml
title:
tier: kb-topic | feature | subsystem | system
status: draft | grilled | accepted
context: <link to CONTEXT.md>
```

## Core sections (all tiers)

- Problem and intent: what this is, why, and what success looks like.
- Scope: in scope, out of scope.
- Bounded contexts: the distinct parts and the lines between them.
- Domain model and ubiquitous language: the key concepts; terms are defined in `CONTEXT.md`, not
  repeated here.
- Dependency direction: which way dependencies point; ports and adapters; what each part owns.
- Data flow: how data moves through the parts.
- Key decisions: the hard choices; each hard-to-reverse one becomes an ADR.
- Open questions: unresolved items, each with a deferral reason or a decision-needed-by boundary.

## Extended sections (subsystem and system tiers; mark `N/A` when not relevant)

- Persistence patterns
- Error handling and resilience
- Observability (SLIs/SLOs, metrics, tracing, logging)
- Testing strategy
- Security and compliance
- Configuration and secrets
- Operations and runbooks
- Scaling path
- Retention and lifecycle

## Concrete technical layer (system tier, optional)

Technology stack, repository structure, concrete domain model, deployment. The
`GTS-Technical-Architecture.md` altitude.

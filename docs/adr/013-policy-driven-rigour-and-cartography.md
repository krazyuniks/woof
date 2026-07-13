---
type: adr
status: accepted
date: 2026-06-28
---

# ADR-013: Rigour and Cartography Are Policy-Driven Capabilities

## Context

Woof's richer runs need contract readiness, conformance checks, and cartography. Smaller pre-decomposed maintenance runs need the same engine but may not need the full cartography floor. Treating these as execution modes would fork the engine path.

## Decision

Rigour is data and policy driven. Project policy lives in `~/.woof/config/projects/<project-key>.toml` and declares the selected delivery profile, project verification command, producer/reviewer run-profile slots, deterministic gate/check floor, and cartography floor. Epic content and work-unit trace fields activate the relevant checks. The engine path remains one loop over `work_units[]`.

Cartography stays first-class. It is enforced when policy requires it and consumed when present.

## Consequences

- There are no "lean mode" or "cartography mode" engine branches.
- Sparse and rich work both use the same execution kernel.
- Missing required cartography fails preflight or opens a structural halt.
- Structural cartography remains the path for impact-aware review and conformance checks.

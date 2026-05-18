# Discovery playbooks

Stage-1 (Discovery) reference material for `/wf`. Each playbook is a self-contained prompt the primary route can read into context to drive a specific kind of investigation:

- `ask-me-questions.md` — adaptive intake; converts vague sparks into structured framing one question at a time.
- `research/` — eight angles on "what do we know already": history (prior attempts), feasibility (constraints), options (compare alternatives), technical (how-to), deep-dive (full investigation), competitive (other players), landscape (the space), open-source (existing tools).
- `consider/` — twelve thinking lenses for stress-testing a framing: 10-10-10, 5-whys, Eisenhower matrix, first-principles, inversion, Occam's razor, one-thing, opportunity-cost, Pareto, second-order, SWOT, via-negativa.

These are *building blocks*, not a fixed sequence. The graph selects the producer node and the primary route uses the playbooks that match the spark and the open questions surfacing during synthesis.

`synthesis.md`, `definition.md`, and `breakdown.md` are the graph-owned producer-node prompts for Stage 1 synthesis, Stage 2 Definition, and Stage 3 Breakdown. They describe node-local output contracts only; the Python graph owns successor selection.

Output of Stage 1 is `discovery/synthesis/{CONCEPT,PRINCIPLES,ARCHITECTURE,OPEN_QUESTIONS}.md` per the EPIC schema's required pre-Definition state.

## Origin

Ported from [taches-cc-resources](https://github.com/lex-christopherson/taches-cc-resources) (MIT, © 2025 Lex Christopherson). See the repo's `ACKNOWLEDGEMENTS.md` for the full attribution.

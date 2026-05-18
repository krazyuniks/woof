# Planning playbooks

Stage 3 planning prompts are graph-owned producer-node prompts. The graph dispatches the primary route with a typed planning input payload, then validates the declared output files and selects the next node.

- `breakdown.md` - Stage 3 Breakdown producer prompt. It reads the declared `EPIC.md` contract and writes only the declared `plan.json`; the Python graph renders `PLAN.md`, dispatches review, and opens the mandatory plan gate.

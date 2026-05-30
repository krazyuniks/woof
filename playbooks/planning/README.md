# Planning playbooks

Stage 3 planning prompts are graph-owned producer-node prompts. The graph returns a typed dispatch contract; the skill dispatches the producer role, then the graph validates the declared output files and selects the next node.

- `breakdown.md` - Stage 3 Breakdown producer prompt. It reads the declared `EPIC.md` contract and writes only the declared `plan.json`; the Python graph renders `PLAN.md`, dispatches review, and opens the mandatory plan gate.

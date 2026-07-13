---
type: discovery-playbook
bucket: research
name: feasibility
summary: Reality check - can this be done within the constraints?
---

# Feasibility

Honest reality check on the spark: can it be done given technical, resource, and
external constraints?

## Process

1. State clearly what is being assessed. Record any constraint you had to assume.
2. Evaluate technical feasibility: known approaches, technology maturity, risks.
3. Evaluate resource feasibility: skills, budget, tools, infrastructure.
4. Evaluate external-dependency feasibility: APIs, services, third-party data.
5. Identify blockers and de-risking strategies.
6. Reach a go / go-with-conditions / no-go verdict.

## Output

Write the artefact as `feasibility.md` into the `discovery/research/` bucket directory declared in
the graph-owned input. Use this shape:

```
## Feasibility Assessment: <subject>

### Strategic summary
<2-3 sentences: verdict, main concern, key condition for success>

### What is being assessed
<clear description, including recorded assumptions>

### Technical feasibility
<known approaches, maturity, risks> - Verdict: Feasible / Risky / Not feasible

### Resource feasibility
<skills, budget, tools> - Verdict: Feasible / Risky / Not feasible

### External-dependency feasibility
<APIs, services, data> - Verdict: Feasible / Risky / Not feasible

### Blockers
| Blocker | Severity | Mitigation |
|---|---|---|

### De-risking options
- <option>: <how it reduces risk, what it costs>

### Overall verdict
<Go / Go with conditions / No-go> - <reasoning and key conditions>

### Sources
- <source>: <url> - <date accessed>
```

## Success criteria

- The assessment is honest, neither optimistic nor pessimistic.
- All three dimensions are evaluated.
- Blockers are specific and addressable.
- The verdict follows from the analysis.

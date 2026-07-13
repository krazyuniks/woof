---
type: discovery-playbook
bucket: thinking
name: swot
summary: Map strengths, weaknesses, opportunities, and threats
---

# SWOT

Apply SWOT analysis to the spark and the emerging direction. Map internal
factors (strengths and weaknesses) and external factors (opportunities and
threats) to inform strategy.

## Process

1. Define the subject being analysed: project, decision, or position.
2. Identify internal strengths: advantages within your control.
3. Identify internal weaknesses: disadvantages within your control.
4. Identify external opportunities: favourable conditions outside your control.
5. Identify external threats: unfavourable conditions outside your control.
6. Develop strategies that connect the quadrants.

## Output

Write the artefact as `swot.md` into the `discovery/thinking/` bucket directory declared in the
graph-owned input. Use this shape:

```
## SWOT: <subject>

### Strengths (internal, positive)
- <strength>: how to leverage it

### Weaknesses (internal, negative)
- <weakness>: how to mitigate it

### Opportunities (external, positive)
- <opportunity>: how to capture it

### Threats (external, negative)
- <threat>: how to defend against it

### Strategic moves
- Strength-opportunity: use <strength> to capture <opportunity>
- Weakness-opportunity: address <weakness> to enable <opportunity>
- Strength-threat: use <strength> to counter <threat>
- Weakness-threat: minimise <weakness> to avoid <threat>
```

## Success criteria

- Correctly categorises internal versus external factors.
- Factors are specific and actionable, not generic.
- Strategies connect multiple quadrants.
- Balances optimism with risk awareness.

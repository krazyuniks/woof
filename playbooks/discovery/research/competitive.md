---
type: discovery-playbook
bucket: research
name: competitive
summary: Research competitors - who else does this, how, strengths and weaknesses
---

# Competitive

Understand who else solves the problem in the spark, how they do it, and where
the opportunities are.

## Process

1. Define the problem or space being competed in.
2. Identify 3-5 relevant competitors, direct and indirect.
3. For each, analyse how they solve the problem, who they serve, their
   strengths and weaknesses, and their business model.
4. Identify patterns shared across competitors (table stakes).
5. Find gaps and differentiation opportunities.

## Output

Write the artefact as `competitive.md` into the `.woof/epics/E<N>/discovery/research/` bucket directory declared in
the graph-owned input. Use this shape:

```
## Competitive Research: <space>

### Strategic summary
<2-3 sentences: the landscape, key insight, main opportunity>

### Problem being solved
<the job these products do for users>

### Competitors
**<competitor>**
- Solution: <how they solve it>
- Target: <who they serve>
- Strengths: <what they do well>
- Weaknesses: <where they fall short>
- Model: <pricing or business model>

### Comparison matrix
| Aspect | Comp 1 | Comp 2 | Comp 3 |
|---|---|---|---|

### Patterns (table stakes)
<what most or all competitors do>

### Gaps and opportunities
- <gap>: <why it is underserved, the opportunity>

### Differentiation options
- <way to differentiate>: <tradeoff>

### Sources
- <source>: <url> - <date accessed>
```

## Success criteria

- Competitors are genuinely relevant, not just well-known names.
- Analysis is honest and not dismissive of competition.
- Gaps are real opportunities, not just missing features.
- Differentiation options are actionable.

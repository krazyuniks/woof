---
type: discovery-playbook
bucket: thinking
name: occams-razor
summary: Find the simplest explanation that fits all the facts
---

# Occam's Razor

Apply Occam's Razor to a situation in the spark or the emerging direction. Among
competing explanations, prefer the one with the fewest assumptions. Simplest is
not easiest; simplest means fewest moving parts.

## Process

1. List the possible explanations or approaches.
2. For each, count the assumptions it requires.
3. Identify which assumptions are actually supported by evidence.
4. Eliminate explanations that need unsupported assumptions.
5. Select the simplest explanation that still fits all the observed facts.

## Output

Write the artefact as `occams-razor.md` into the `discovery/thinking/` bucket directory declared in
the graph-owned input. Use this shape:

```
## Occam's Razor: <situation>

### Candidate explanations
1. <explanation>: requires assumptions <list>

### Evidence check
- <assumption>: <supported / unsupported>

### Simplest valid explanation
<the one with the fewest unsupported assumptions>

### Why it wins
<what it explains without extra machinery>
```

## Success criteria

- Enumerates all plausible explanations.
- Makes assumptions explicit and countable.
- Distinguishes supported from unsupported assumptions.
- Does not oversimplify; the chosen explanation must fit all the facts.

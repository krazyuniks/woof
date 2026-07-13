---
type: discovery-playbook
bucket: thinking
name: inversion
summary: Solve problems backwards - what would guarantee failure?
---

# Inversion

Apply inversion to the spark and the emerging direction. Instead of asking how
to succeed, ask what would guarantee failure, then design to avoid those things.

## Process

1. State the goal or desired outcome.
2. Invert it: what would guarantee failure?
3. List the failure modes thoroughly and honestly.
4. For each failure mode, identify the avoidance strategy.
5. Build the success plan by systematically avoiding failure.

## Output

Write the artefact as `inversion.md` into the `discovery/thinking/` bucket directory declared in the
graph-owned input. Use this shape:

```
## Inversion: <goal>

### Goal
<what success looks like>

### Guaranteed failure modes
1. <way to fail>: avoid by <specific action>

### Anti-goals (never do)
- <behaviour to eliminate>

### Success by avoidance
<why not doing the failure modes makes success much more likely>

### Remaining risk
<what is left after avoiding the obvious failures>
```

## Success criteria

- Failure modes are specific and realistic.
- Avoidance strategies are actionable.
- Surfaces risks that optimistic planning misses.
- Creates clear never-do boundaries.

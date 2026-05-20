---
type: discovery-playbook
bucket: thinking
name: eisenhower-matrix
summary: Apply the Eisenhower matrix (urgent / important) to prioritise
---

# Eisenhower Matrix

Apply the Eisenhower matrix to the tasks or decisions raised by the spark and
the emerging direction. Categorise items by urgency and importance to clarify
what to do now, schedule, delegate, or drop.

## Process

1. List the tasks, decisions, or items in scope.
2. Evaluate each on two axes: important (contributes to long-term goals) and
   urgent (needs immediate attention or has deadline pressure).
3. Place each item in the appropriate quadrant.
4. Give a specific action for each quadrant.

## Output

Write the artefact as `eisenhower-matrix.md` into the `.woof/epics/E<N>/discovery/thinking/` bucket directory declared
in the graph-owned input. Use this shape:

```
## Eisenhower Matrix: <scope>

### Q1 Do first (important, urgent)
- <item>: <specific action, deadline if any>

### Q2 Schedule (important, not urgent)
- <item>: <when to do it, why it matters long-term>

### Q3 Delegate (not important, urgent)
- <item>: <who or what can handle it, or how to minimise time spent>

### Q4 Eliminate (not important, not urgent)
- <item>: <why it is noise, permission to drop it>

### Immediate focus
<one sentence on what to tackle right now>
```

## Success criteria

- Every item is clearly placed in one quadrant.
- Q1 items have specific next actions.
- Q4 items are explicitly marked as droppable.
- Reduces overwhelm by creating a clear action hierarchy.

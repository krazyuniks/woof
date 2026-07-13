---
type: discovery-playbook
bucket: thinking
name: 5-whys
summary: Drill to root cause by asking why repeatedly
---

# 5 Whys

Apply the 5 Whys technique to a problem in the spark or the emerging direction.
Keep asking "why" until you reach the root cause, not just a symptom.

## Process

1. State the problem clearly.
2. Ask "why does this happen?" and record the answer.
3. Ask "why?" about that answer, and again about each answer after it.
4. Continue until you reach a root cause, usually within five iterations.
5. Identify an actionable intervention at the root.

## Output

Write the artefact as `5-whys.md` into the `discovery/thinking/` bucket directory declared in the
graph-owned input. Use this shape:

```
## 5 Whys: <problem>

### Problem
<clear statement>

### Why chain
- Why 1: <surface cause>
- Why 2: <deeper cause>
- Why 3: <even deeper>
- Why 4: <approaching root>
- Why 5: <root cause>

### Root cause
<the actual thing to fix>

### Intervention
<specific action at the root level>
```

## Success criteria

- Moves past symptoms to the actual cause.
- Each "why" digs genuinely deeper.
- Stops at an actionable root rather than infinite regress.
- The intervention addresses the root, preventing recurrence.

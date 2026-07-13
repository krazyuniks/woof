---
type: discovery-playbook
bucket: thinking
name: second-order
summary: Think through consequences of consequences
---

# Second-Order Thinking

Apply second-order thinking to the spark and the emerging direction. First-order
thinking stops at immediate effects; second-order thinking keeps asking "and
then what?" and follows the chain.

## Process

1. State the action or decision.
2. Identify the first-order effects: immediate, obvious consequences.
3. For each first-order effect, ask "and then what happens?"
4. Continue to third-order effects where they are significant.
5. Identify delayed consequences that change the calculus.
6. Assess whether the action is still worth it after the full chain.

## Output

Write the artefact as `second-order.md` into the `discovery/thinking/` bucket directory declared in
the graph-owned input. Use this shape:

```
## Second-Order Thinking: <action>

### Action
<what is being considered>

### First-order effects (immediate)
- <effect>

### Second-order effects (and then what?)
- <effect> leads to <consequence>

### Third-order effects
- <key downstream consequences>

### Delayed consequences
<effects that are not obvious initially but matter long-term>

### Revised assessment
<whether the action is still worth it after tracing the chain>
```

## Success criteria

- Traces causal chains beyond the obvious effects.
- Identifies feedback loops and unintended consequences.
- Reveals delayed costs or benefits.
- Distinguishes actions that compound well from those that do not.

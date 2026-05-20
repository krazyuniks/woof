---
type: discovery-playbook
bucket: thinking
name: opportunity-cost
summary: Analyse what is given up by choosing this option
---

# Opportunity Cost

Apply opportunity-cost analysis to a choice in the spark or the emerging
direction. Every yes is a no to something else; surface the true cost of the
choice.

## Process

1. State the choice being considered.
2. List the resources it consumes: time, money, energy, attention.
3. Identify the best alternative use of those same resources.
4. Compare the value of the chosen option against the best alternative.
5. Decide whether the tradeoff is worth it.

## Output

Write the artefact as `opportunity-cost.md` into the `.woof/epics/E<N>/discovery/thinking/` bucket directory declared
in the graph-owned input. Use this shape:

```
## Opportunity Cost: <choice>

### Choice
<what is being considered>

### Resources required
- Time: <estimate>
- Money: <estimate>
- Energy and attention: <cognitive load>
- Other: <relationships, reputation, etc.>

### Best alternative uses
- With that <resource>, could instead: <alternative and its value>

### True cost
<choosing this means not doing the best alternative, which would have provided
what value>

### Verdict
<is the chosen option worth more than the best alternative?>
```

## Success criteria

- Makes hidden costs explicit.
- Compares against the best alternative, not just any alternative.
- Accounts for all resource types, not only money.
- Reveals when "affordable" things are actually expensive.

---
type: discovery-playbook
bucket: thinking
name: first-principles
summary: Break down to fundamentals and rebuild from base truths
---

# First Principles

Apply first-principles thinking to the spark and the emerging direction. Strip
away assumptions, conventions, and analogies, identify the fundamental truths,
then rebuild understanding from those.

## Process

1. State the belief or approach being examined.
2. List every current assumption, including the "obvious" ones.
3. Challenge each assumption: is it actually true, and why?
4. Identify the base truths that cannot be reduced further.
5. Rebuild the solution from only those fundamentals.

## Output

Write the artefact as `first-principles.md` into the `.woof/epics/E<N>/discovery/thinking/` bucket directory declared
in the graph-owned input. Use this shape:

```
## First Principles: <subject>

### Current assumptions
- <assumption>: <challenged: true / false / partial>

### Fundamental truths
- <truth>: <why it is irreducible>

### Rebuilt understanding
<what follows when reasoning only from the fundamentals>

### New possibilities
<options that emerge once legacy assumptions are dropped>
```

## Success criteria

- Surfaces hidden assumptions.
- Distinguishes convention from necessity.
- Identifies irreducible base truths.
- Opens solution paths not visible before, without reasoning by analogy.

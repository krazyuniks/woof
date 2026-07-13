---
type: discovery-playbook
bucket: research
name: technical
summary: Research how to implement something - approaches, libraries, tradeoffs
---

# Technical

Research concrete ways to build what the spark describes: libraries, patterns,
and architectures, each with honest tradeoffs.

## Process

1. Restate what needs to be built and the constraints visible in the spark and
   discovery context. Where a constraint is unstated, record the assumption.
2. Identify 2-4 genuinely different implementation approaches.
3. For each approach, research how it works, the libraries or tools involved,
   its complexity, its performance characteristics, and its maintenance status.
4. Compare the tradeoffs honestly.
5. Recommend an approach for the stated context.

## Output

Write the artefact as `technical.md` into the `discovery/research/` bucket directory declared in the
graph-owned input. Use this shape:

```
## Technical Research: <topic>

### Strategic summary
<2-3 sentences: the approaches, recommendation, key tradeoff>

### Requirements and assumptions
- <requirement or recorded assumption>

### Approach <n>: <name>
- How it works: <brief explanation>
- Libraries/tools: <specific packages, versions>
- Pros: <advantages>
- Cons: <disadvantages>
- Best when: <use-case fit>
- Complexity: S / M / L

### Comparison
| Aspect | Approach 1 | Approach 2 | Approach 3 |
|---|---|---|---|
| Complexity | | | |
| Performance | | | |
| Maintainability | | | |

### Recommendation
<which approach and why, tied to the stated context>

### Sources
- <source>: <url> - <date accessed>
```

## Success criteria

- Approaches are genuinely different, not variations of one thing.
- Tradeoffs are honest, not sales copy.
- Libraries are specific and current.
- The recommendation fits the stated constraints and assumptions.

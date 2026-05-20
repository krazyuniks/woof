---
type: discovery-playbook
bucket: research
name: history
summary: Research what has been tried before - past attempts, lessons learned
---

# History

Find what has been tried before for the problem in the spark, internally and
externally, and extract lessons so the epic does not repeat known mistakes.

## Process

1. Define the problem or approach being investigated.
2. Find past attempts: internal projects, industry examples, academic work.
3. For each attempt, document what was tried, what worked, what failed and why,
   and what is different now.
4. Extract the common success factors and failure modes.
5. State what to adopt and what to avoid.

## Output

Write the artefact as `history.md` into the `.woof/epics/E<N>/discovery/research/` bucket directory declared in the
graph-owned input. Use this shape:

```
## History Research: <problem or approach>

### Strategic summary
<2-3 sentences: key historical pattern, main lesson, what is different now>

### Past attempts
**<attempt: name, company, or project>**
- When: <timeframe>
- What they tried: <approach>
- What worked: <successes>
- What failed: <failures and root causes>
- Why: <analysis of the success or failure factors>

### Patterns
- Common success factors: <factors>
- Common failure modes: <why things typically fail>

### What is different now
- <technology, market, or context change>: <implication>

### Lessons to apply
- Do: <lesson to adopt>
- Do not: <mistake to avoid>

### Sources
- <source>: <url> - <date accessed>
```

## Success criteria

- Past attempts are relevant to the same problem and context.
- Failure analysis reaches root cause, not surface symptom.
- Lessons are actionable, not just "be careful".
- Acknowledges what has changed since the past attempts.

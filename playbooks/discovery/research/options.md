---
type: discovery-playbook
bucket: research
name: options
summary: Compare multiple options side-by-side with a recommendation
---

# Options

Structured side-by-side comparison to support an informed decision. Works for
tools, approaches, vendors, or architectures raised by the spark.

## Process

1. Define the decision criteria: what actually matters for this choice. Weight
   each criterion by importance.
2. List the viable options.
3. Evaluate each option against each criterion.
4. Recommend an option, with a runner-up and the condition under which the
   runner-up wins.

## Output

Write the artefact as `options.md` into the `.woof/epics/E<N>/discovery/research/` bucket directory declared in the
graph-owned input. Use this shape:

```
## Options Comparison: <decision>

### Strategic summary
<2-3 sentences: the options, recommendation, key tradeoff>

### Context
<what is being decided and why it matters>

### Decision criteria
1. <criterion> - <why it matters> - Weight: High / Med / Low

### Options
**Option <n>: <name>**
- <criterion>: <rating and brief note>
- Score: <x>/10

### Comparison matrix
| Criterion | Option A | Option B | Option C |
|---|---|---|---|

### Recommendation
<option> because <reasoning tied to the weighted criteria>

### Runner-up
<option> - choose this if <specific condition>

### Sources
- <source>: <url> - <date accessed>
```

## Success criteria

- Criteria reflect what actually matters for the decision.
- Options are genuinely comparable, like for like.
- Ratings are justified, not arbitrary.
- The recommendation follows from the analysis and a runner-up gives a
  contingency.

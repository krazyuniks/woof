---
type: discovery-playbook
bucket: research
name: landscape
summary: Map the space - tools, players, trends, gaps in a domain
---

# Landscape

Map the landscape implied by the spark and discovery context: who the players
are, what tools exist, where the space is heading, and where the gaps are.

## Process

1. Define the space and its scope boundaries (what is in, what is out).
2. Identify the categories within the space.
3. Map the established players, emerging players, and key tools per category.
4. Identify trends and direction of travel.
5. Find gaps and underserved white space.
6. State what the map implies for this epic.

## Output

Write the artefact as `landscape.md` into the `discovery/research/` bucket directory declared in the
graph-owned input. Use this shape:

```
## Landscape Map: <space>

### Strategic summary
<2-3 sentences: shape of the space, key trend, main opportunity>

### Scope
<what is included and excluded>

### Categories
**<category>**
- Established players: <names>
- Emerging players: <names>
- Key tools: <tools>
- Trend: <where this category is heading>

### Trends
- <trend>: <what is happening, implications>

### Gaps and white space
- <gap>: <why it is underserved, opportunity size>

### Implications for this epic
- <where this work could fit, compete, or differentiate>

### Sources
- <source>: <url> - <date accessed>
```

## Success criteria

- Categories are mutually exclusive and collectively exhaustive.
- Players are correctly positioned.
- Trends are backed by evidence, not assertion.
- Gaps are genuine opportunities, not just missing features.
- Implications connect the map back to the spark.

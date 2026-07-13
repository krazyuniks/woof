---
type: discovery-playbook
bucket: research
name: deep-dive
summary: Comprehensive investigation of a topic - thorough analysis with sources
---

# Deep Dive

Investigate one topic from the spark beyond surface level. Synthesise multiple
sources into a coherent, comprehensive understanding.

## Process

1. Define the scope and the key questions the investigation must answer.
2. Gather information from multiple angles: how it works (mechanics), why it
   exists (history, motivation), how it is used (patterns), where it fails
   (limitations, edge cases), and what is next (trends).
3. Synthesise the angles into one coherent understanding.
4. Identify the unknowns that remain.

## Output

Write the artefact as `deep-dive.md` into the `discovery/research/` bucket directory declared in the
graph-owned input. Use this shape:

```
## Deep Dive: <topic>

### Strategic summary
<2-3 sentences: what this is, key insight, main implication for the epic>

### Key questions
- <question this research answers>

### Overview
<short synthesis of what this is and why it matters>

### How it works
<mechanics, architecture, or process>

### History and context
<why it exists, what problem it solved, how it evolved>

### Patterns and best practices
- <pattern>: <when and why>

### Limitations and edge cases
- <limitation>: <workaround or mitigation>

### Current state and trends
<where things are heading>

### Key takeaways
1. <most important insight>

### Remaining unknowns
- [ ] <question that still needs answering>

### Sources
- <source>: <url> - <date accessed>
```

## Success criteria

- Answers the key questions thoroughly.
- Goes past "what" to "why" and "when".
- Identifies limitations honestly.
- Is explicit about what remains unknown.

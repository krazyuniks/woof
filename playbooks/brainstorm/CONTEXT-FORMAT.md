<!-- VENDORED from agent-toolkit skills/brainstorm - do not edit here. Regenerate with `just vendor-brainstorm`. -->

# CONTEXT.md format

`CONTEXT.md` is the project's glossary, the single home for its ubiquitous language. The
architecture and design documents reference it rather than repeating definitions.

## Structure

- Header: the context name and a one or two sentence statement of what it covers.
- Terminology: a list of glossary entries.

## Entry format

```
**<Term>**
<Definition in one or two sentences. State what it is, not what it does.>
Avoid: <words this term rejects>
```

## Rules

- Be opinionated. Choose one preferred term and list the alternatives under `Avoid`.
- Project-specific concepts only. Do not define general programming patterns.
- Create it lazily, when the first term resolves.
- One `CONTEXT.md` per bounded context. For several contexts, a `CONTEXT-MAP.md` at the root lists
  the contexts, their locations, and their relationships.

Adapted from the grill-with-docs skill (`mattpocock/skills`). See `ACKNOWLEDGEMENTS.md`.

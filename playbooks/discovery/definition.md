# Epic Definition Producer Node

You are the producer role for a Woof `epic_definition` graph node.

Graph-owned input:

```json
{planning_input_json}
```

## Context documents — read these first

When repo policy supplies cartography, the graph delivers the names of the relevant cartography documents in `inputs.cartography_paths`. The documents live in the project's cartography directory in the operator home. Read them before beginning work:

- `CURRENT-ARCHITECTURE.md`
- `STRUCTURE.md`
- `CONCERNS.md`
- `TARGET-ARCHITECTURE.md`
- `PRINCIPLES.md`

Read the declared synthesis directory and produce only `EPIC.md` at the declared `epic_path`.

`EPIC.md` must start with YAML front matter matching `schemas/epic.schema.json`:

- `epic_id`
- `title`
- `intent`
- `observable_outcomes`
- `contract_decisions`
- `acceptance_criteria`
- `open_questions` object entries for unresolved `OQ<n>` discovery questions deliberately carried forward, each with `id`, `question`, and `deferral_reason`
- `resolved_open_questions` object entries for discovery questions resolved during Definition, each with `id` and `resolution`

Author a planning-ready contract:

- observable outcomes include concrete verification signals;
- acceptance criteria are machine-checkable: name the relevant `O<n>` or `CD<n>` plus a command, test marker, repo path, `module.py:Symbol`, comparison, number+unit, or concrete observable assertion;
- contract decisions cite exact paths, schema refs, API refs, or explicitly mark forward-created surfaces;
- references to existing paths and symbols are real in the current repository;
- forward-created references use an exact annotation outside the backticks, for example `` `path/to/file` (forward-created) `` or `` `path/to/file` (created by ticket <id>) ``. Put this marker in the contract-decision `notes` or EPIC body next to the exact path or symbol token; loose prose such as "future surface" is not enough;
- subjective terms such as "good UX", "robust", or "performant" are paired with measurable assertions.

Advisory (prose body, not a required front-matter field): for each outcome where it helps, name the highest existing test seam at which the outcome could be verified - the public function, CLI command, HTTP route, or module boundary a test would target. Decide these seams while writing the contract rather than leaving them to be discovered during the build; they orient breakdown and execution without locking implementation. Keep this in the prose body unless and until E2 adds an optional seam field to `epic.schema.json`.

The prose body may add context for a human reader, but the front matter is the contract.

YAML safety rule: quote front-matter strings that contain Markdown syntax,
backticks, colons, brackets, hashes, or leading punctuation. If unsure, quote every string value in front matter. Do not start an unquoted scalar with a
backtick or other YAML-significant character.

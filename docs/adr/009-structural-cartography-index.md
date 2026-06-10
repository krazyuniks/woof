---
type: adr
status: accepted
date: 2026-06-07
---

# ADR-009: Structural Cartography Index

## Context

ADR-004 made cartography mandatory and established the `.woof/codebase/` layers:
human-authored design docs, mapper-authored AS-IS docs, and a post-commit mechanical
layer. The current mechanical layer is deliberately small: `tags`, `files.txt`, and
`freshness.json`.

The code-mapping landscape research in `docs/research/code-mapping-landscape.md`
shows the same gap across Woof, GSD, and the surveyed tools: prose plus ctags is useful
orientation, but it cannot answer structural questions such as:

- who calls this symbol;
- what imports or depends on this file;
- what is the likely blast radius of this changed symbol or file;
- which neighbouring symbols are worth loading into a producer or reviewer prompt.

The research also shows what not to adopt. Graph databases, always-on daemons, and
parallel MCP surfaces add operational weight and compete with Woof's topology. The
useful transfer is a local, deterministic, queryable structural artefact that the
Python engine can regenerate and read.

The 2026-06-07 spike and `docs/research/code-mapping-landscape.md` settled the
V1 extraction direction: tree-sitter is the primary substrate. Python `ast` remains
available only as an adapter-compatible fallback when the tree-sitter substrate is
unavailable, not as a co-equal first path.

## Decision

Woof will add a structural cartography index as an extension of the ADR-004 mechanical
layer.

The index is:

- **Local and gitignored.** It lives under `.woof/codebase/structural/` and is
  regenerated from source, like `tags` and `files.txt`.
- **Python-owned.** The extraction, schema, and query surface live behind the `woof`
  CLI. Woof does not vendor a TypeScript agent tool, expose a separate MCP server, or
  delegate runtime state to another process.
- **Queryable, not authoritative orchestration.** The index informs prompt context,
  readiness checks, reviewer evidence, and audits. It does not choose graph successors
  or mutate epic state.
- **Advisory where static analysis is uncertain.** Tree-sitter or AST facts can be
  deterministic, but cross-file call resolution in dynamic languages is not complete.
  Edges carry provenance/confidence such as `EXTRACTED`, `HEURISTIC`, and `AMBIGUOUS`.
  Confidence never becomes gate logic by itself.

### Storage

The default storage target is SQLite at `.woof/codebase/structural/index.sqlite`.
SQLite is an embedded file format, not an external graph database. It gives Woof enough
queryability for callers/callees/impact and leaves room for FTS without adding a daemon.

The first schema is intentionally small:

- `files`: repo-relative path, language, content hash, indexed timestamp.
- `symbols`: stable symbol id, kind, name, qualified name, file path, line span,
  signature or outline text where cheaply available.
- `edges`: source symbol/file, target symbol/file, relation, provenance/confidence,
  source location, reason.
- `meta`: generator version, git ref, indexed languages, schema version.

Symbol identity must be stable enough for audit and prose references. Line numbers are
metadata, not the primary identity. A line shift should not create a new identity when
the module and qualified name are unchanged.

### Scope

V1 is Python-first in language scope so Woof can dogfood on itself, but the extractor
substrate is tree-sitter-first. If a local environment cannot supply the tree-sitter
substrate, the implementation may fall back to Python `ast` behind the same adapter
boundary; that fallback must not become a second parser/indexer path.

V1 targets:

- symbol outlines: modules, classes, functions, methods, line spans, signatures;
- `contains` and `defines` edges;
- import/dependency edges;
- high-confidence direct calls where they can be resolved safely.

The implementation should prefer a precise, incomplete graph over a noisy graph that
looks complete. Ambiguous or heuristic edges are allowed only when labelled. In
particular, unqualified simple-name matching of common method names (`append`, `get`,
`run`, ...) produces near-worthless `HEURISTIC` edges - every `list.append()` collides
with the one repo method named `append` - and must be suppressed or heavily down-ranked.
Resolving a method call on a non-`self` receiver (`obj.method()`) needs type information,
which is the LSP-backed executor's domain rather than the parser's; V1 labels these
rather than guessing.

The spike's LSP-assisted resolution pass over unresolved `obj.method()` sites is
deferred. It is a candidate `HEURISTIC` -> `EXTRACTED` upgrade pass to decide during
E13 with eval data, not part of the E12 V1 extractor contract.

### Freshness and reproducibility

The structural index is mechanical-layer state: `scripts/refresh-cartography` regenerates
it every commit, so within an epic it tracks HEAD rather than freezing. That is deliberate
and correct for the inner loop - the reviewer and producer of a later story should see the
structural reality earlier committed stories created, not an epic-start snapshot. ADR-004's
epic-stability rule governs the prose layers (design and AS-IS docs); it does not freeze
structural facts.

Reproducibility comes from determinism, not snapshotting. Extraction is a pure function of
the source at a `git ref` plus the recorded `meta` (generator version, parser/grammar
version, indexed languages, schema version). Any historical index state is therefore
rebuildable from the commit for audit or replay, so the index never needs to be versioned
or committed.

### Query Surface

The operator and dispatched nodes consume the index through `woof cartography`, not
through `woof graph`, MCP, or direct SQLite access.

Initial read-only verbs:

- `woof cartography symbols ...`
- `woof cartography callers ...`
- `woof cartography callees ...`
- `woof cartography impact ...`
- `woof cartography context ...`
- `woof cartography stats`

Each verb should support machine-readable JSON and token-bounded plain text suitable
for prompt assembly.

Stage-5 reviewer integration is the first prompt consumer: staged diff -> changed
files/symbols -> blast-radius context -> critique prompt. Producer integration and
mapper grounding follow only after the reviewer path is measurable.

## Consequences

- ADR-004's three-layer cartography model remains intact. The structural index is a
  richer mechanical artefact, not a replacement for design docs or AS-IS prose.
- `scripts/refresh-cartography` eventually regenerates the structural index alongside
  `tags`, `files.txt`, and `freshness.json`. Until that implementation lands, the
  mandatory mechanical files remain the ADR-004 baseline.
- The research recommendations are split into separate implementation tracks:
  structural index first, reviewer impact integration second, semantic retrieval and
  ranking later.
- Semantic retrieval is not bundled into V1. A Semble-style BM25/static-embedding
  retriever is complementary and should land as a separate artefact and epic.
- Onboarding features such as community detection, hub detection, bottom-up mapper
  grounding, and symbol-linked AS-IS sections depend on the structural index but are
  not part of V1.
- No graph DB, always-on daemon, or parallel MCP tool surface is introduced.

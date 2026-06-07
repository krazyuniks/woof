# Code-mapping landscape: augmenting Woof cartography

Status: research complete, 2026-06-06. Synthesised into ADR-009 and backlog epics
E12-E15 on 2026-06-07.

Raw per-project notes and the full comparison matrix live in the vault:
`~/Work/vault/records/radianit/projects/woof/research/code-mapping/` (one slug-named file per
project, plus `MATRIX.md` and `00-gsd-provenance.md`). This document is the distilled,
decision-oriented synthesis.

Scope: the seven projects Ryan flagged (graphify, code-review-graph, truecourse, GitNexus,
gortex, semble, codegraph) plus six curated comparators that define the agentic
code-mapping space (Aider repo-map, Serena, Sourcegraph SCIP + GitHub stack-graphs, CodeQL,
Potpie, blarify). 13 triaged via web/docs; the top 3 by Woof-relevance (graphify, GitNexus,
codegraph) deep-dived at code level.

## TL;DR

Woof's cartography descends directly from GSD's `/gsd:map-codebase` (same author as
`taches-cc-resources`): seven LLM-authored prose docs, plus Woof's own additions of a human
design layer and a deterministic mechanical layer (ctags `tags` + `files.txt` +
`freshness.json`). That gives us a **narrative** layer and one **lexical** index (symbol name
-> file:line). It gives us **nothing relational, semantic, or queryable**: no call/import
graph, no "what breaks if I change this", no "find the retry logic by meaning".

The surveyed projects converge on the same useful pattern for that gap:

> Add a **deterministic, tree-sitter-derived call/import/edge graph** as a new artefact in
> Woof's *mechanical* cartography layer - gitignored, regenerated per commit, beside ctags -
> and expose a thin `woof` query verb (callers / callees / impact / neighbours) that hands
> dispatched producer/reviewer nodes a token-bounded subgraph instead of prose. **Not** a graph
> database, **not** a daemon, **not** a parallel MCP tool surface.

ADR-009 records the accepted direction. It is strictly additive to ADR-004 (it enriches
`.woof/codebase/`, never bypasses it) and respects ADR-001/007 (reimplement in Python behind
`woof`, do not vendor a TS tool or stand up a second operator surface). The first cut is scoped
to Python so Woof can dogfood on itself. It starts with a tree-sitter/parser work audit because
there may be concurrent extraction work in flight; if no reusable substrate has landed, the
implementation can begin with a Python `ast` extractor behind the same adapter boundary.

## The gap, precisely (provenance)

See `00-gsd-provenance.md` for the full lineage. The one-line version: the entire GSD/taches
lineage's code-understanding capability is prose mapping; Woof added ctags on top. Every tool
in this study is, in effect, an answer to one of three missing layers:

| Missing layer | What it answers | Tools that supply it |
|---|---|---|
| Relational / precise | who calls X, what imports X, blast radius of a change | graphify, GitNexus, codegraph, blarify, gortex, code-review-graph, Serena (live), SCIP/stack-graphs |
| Semantic | "find the code that does Y" by meaning, not name | semble, (graphify/potpie via LLM) |
| Ranked / prioritised | which symbols matter most for this task | aider-repomap (PageRank) |

## Comparison (decision view)

Full descriptive matrix in `MATRIX.md`. This is the verdict view. Relevance = fit for
augmenting Woof, 1-5. Use-case key: G greenfield pre-planning, O onboarding a large inherited
repo, B BAU daily agentic coding, H human inspection (scores 1-5 in the per-project files).

| Project | Rel | Layer it would add | Best use case | Verdict for Woof |
|---|:--:|---|---|---|
| **graphify** | 5 | persisted call/import graph (networkx JSON) + scoped-context renderer | O, B | **Crib the technique.** Closest to a drop-in: `extract.py` is networkx-only, vendorable/wrappable as a mechanical step (M). Take the node/edge+confidence schema and the token-budgeted subgraph renderer. Drop the viz/whisper/LLM-doc layers. |
| **codegraph** | 5 | AST symbol+edge graph in SQLite + callers/callees/impact | O, B | **Crib the schema + two-pass resolve.** Cleanest minimal schema (nodes + typed edges + FTS5) and explicit extract->resolve model. TS runtime, so reimplement in Python. Full multi-language resolve is L; Python-only scoped cut is M. |
| **GitNexus** | 5 | call/import/inherit graph + impact, confidence-tagged | B, O | **Ideas only (licence: PolyForm Noncommercial).** Borrow the stable content-derived symbol-ID scheme + per-file content-hash incremental writeback (makes per-commit regen cheap) and the depth-bucketed impact output format (WILL BREAK / LIKELY / TRANSITIVE). Do not vendor. |
| **aider-repomap** | 5 | PageRank ranking over a reference graph + token-budgeted skeleton | B | **Crib the ranking idea.** Once we have an edge graph, rank by centrality + personalise to the epic/ticket so each node's prompt gets the most structurally-relevant neighbours. Edges are name-match heuristics, so treat as ranking, not truth. |
| **Serena** | 5 | live LSP symbol nav (find_symbol, find_referencing_symbols) | B | **Reference implementation for ADR-002's LSP producer.** Borrow the persisted symbol-outline cache and outline-first/drill-down token discipline. Its "no persisted graph" stance is its weakness for onboarding - do not copy that. Heavy per-language LSP runtime. |
| **potpie** | 5 | Neo4j knowledge graph + per-symbol LLM descriptions + agents | O | **Ideas only.** Borrow per-symbol one-line descriptions and role-tag faceting (auth/db/ui) as cheap enrichments, kept file-based. Reject the Neo4j+Postgres+Redis+Celery platform - opposite of Woof's ethos. |
| **blarify** | 5 | tree-sitter+LSP -> Neo4j graph, bottom-up doc generation | O, B | **Ideas only.** Borrow the CALLS/IMPORTS/INHERITS/DEFINES/CONTAINS edge taxonomy and the bottom-up context-propagating doc pass (maps onto our parallel mappers). Reject Neo4j + LSP-daemon runtime. |
| **code-review-graph** | 4 | call/import/test graph (SQLite) + blast-radius context selection | B | **Borrow blast-radius-driven context selection** to choose which AS-IS docs/source to inject per changed file, plus token-savings instrumentation. Heavy dep surface; MCP-centric consumption model differs from ours. |
| **gortex** | 4 | in-memory symbol graph + token-frugal serialisation | B | **Borrow the serialisation idea** (compact tab-delimited symbol/edge table for prompts) and git-hash-keyed cache invalidation. Reject the always-on daemon + in-memory-only model. Early, single-author, licence ambiguity. |
| **semble** | 4 | hybrid semantic retrieval (Model2Vec + BM25 + RRF) | B | **Crib for the semantic layer.** CPU-only, no API key, already uv/Python - the cleanest adoption of the lot. Add as a retrieval artefact + `woof` search verb. No structural understanding, so it complements (not replaces) the graph. |
| **SCIP + stack-graphs** | 4 | precise symbol-identity + def/ref/impl index | O, B | **Borrow two conventions:** the stable structured symbol-string scheme (unambiguous, refactor-stable cross-doc references in prose) and stack-graphs' per-file-partial -> stitch incremental model. stack-graphs is archived; full SCIP indexers are build-heavy. Concept, not codebase. |
| **TrueCourse** | 3 | architecture-violation pass + spec->contract->verify | B (+ docs alignment) | **Different problem (drift detection, not orientation),** but the spec->contract->verify idea is a natural Woof check runner (`src/woof/checks/`) for the "docs move with code" concern, and it endorses our committable-baseline + diff design. |
| **CodeQL** | 3 | relational/Datalog semantic query over AST/dataflow | H (audit) | **Concept only.** Validates "treat the mechanical layer as a queryable relational index" and the SARIF stable-results contract. Whole-program, non-incremental, multi-GB, proprietary engine - the cost profile is the opposite of cheap-per-commit. |

## Use-case routing (Ryan's framing)

- **Onboarding a large inherited codebase** (the "we just inherited a big project" case).
  Highest fit: graphify and potpie (O5). Here a heavier *one-shot* pass is justified - you can
  afford an expensive index once. The play: build the edge graph (below), then run community
  detection (Leiden) + hub/"god-node" detection + shortest-path orientation over it to seed the
  AS-IS mapper subagents and the human `TARGET-ARCHITECTURE.md`, so the prose maps are grounded
  in structure rather than guessed. This is a distinct, setup-time capability.
- **BAU daily agentic coding** (the inner loop). Highest fit: GitNexus, codegraph,
  aider-repomap, Serena, code-review-graph, gortex, semble (B5). The play: the *cheap,
  per-commit* edge graph + an impact/blast-radius query + semantic retrieval, all
  token-budgeted, threaded into dispatched-node prompts. This is the everyday win and the
  reason to build the graph at all.
- **Greenfield pre-planning.** Every tool scores low (G1-G2): there is no code to map. Keep
  this on the design layer and `/brainstorm`. The research confirms code-mapping tools add
  little before code exists.
- **Human inspection.** A minor bonus, not Woof's agent-first mission: graphify's HTML/Obsidian
  export, TrueCourse's dashboard, GitNexus/CodeQL visualisations. Build only if a human-facing
  `woof observe`-style view is wanted later.

## Recommended augmentation plan

Phased, each item names where it slots into ADR-004's three layers, the effort, and which
project to crib from. A1+A2 are the core; the rest are independent follow-ons.

- **A1 - Structural index (mechanical layer, E12).** Add `.woof/codebase/structural/index.sqlite`,
  gitignored, regenerated by the post-commit `scripts/refresh-cartography` path once the
  generator exists. Nodes = files/symbols; typed edges start with `{contains, defines, imports,
  calls}`. Edge provenance/confidence is explicit: `EXTRACTED | HEURISTIC | AMBIGUOUS`.
  Define query-output schemas in `schemas/`. Crib graphify's node/edge contract, codegraph's
  two-pass extract->resolve model, GitNexus's stable-ID/content-hash discipline, and Serena's
  symbol-outline-first approach. Scope v1 to Python and prefer precision over apparent
  completeness.
- **A2 - `woof cartography` query verb (CLI + dispatch, E12/E13, needs A1).** Expose
  callers/callees/`impact`(blast-radius)/neighbours/shortest-path with token-budgeted text
  output, and thread it into node prompt assembly. Primary consumer: the Stage-5 reviewer
  (blast-radius before a change; feeds the gate). This is the agentic payoff - precise, fresh,
  token-cheap per-task context instead of whole markdown docs.
- **A3 - Semantic retrieval (mechanical layer, E14, independent).** A semble-style index
  (Model2Vec + BM25 + RRF over tree-sitter chunks), CPU-only, no API key, already uv/Python.
  New artefact + a `woof` search verb returning token-budgeted snippets. Fills the
  "find by meaning" gap ctags and the graph cannot.
- **A4 - Centrality ranking (E14, needs A1).** PageRank/centrality over the edge
  graph + task-personalisation (boost epic/ticket-mentioned files) to prioritise what each
  node's prompt loads. Aider's idea, cheap once the graph exists.
- **A5 - Onboarding pass (E15, setup-time capability, needs A1).** One-shot community
  detection + hub detection + shortest-path over the graph to seed the AS-IS mappers and
  `TARGET-ARCHITECTURE.md` when inheriting a large repo. Distinct from BAU; run at setup.

Cross-cutting, near-free: adopt the SCIP stable-symbol-string convention for how mappers refer
to symbols in prose (refactor-stable cross-doc references), and add token-savings
instrumentation so cartography's payoff is measured.

## Do not adopt

- **Graph databases** (Neo4j - potpie/blarify; LadybugDB - GitNexus; SQLite-as-graph is fine).
  A second persistent engine conflicts with "state on disk, no graph DB, cheap per-commit"
  (ADR-004) and adds operational weight an inner-loop tool should not carry.
- **Always-on daemons / long-running servers** (gortex, potpie platform). Woof loads context at
  dispatch; a daemon is a parallel state path.
- **Parallel MCP tool surfaces** (most tools ship 8-115 MCP tools). Woof's operator entry point
  is the `/woof` umbrella over `woof wf` (ADR-007); a competing MCP surface violates that.
  Reimplement the *technique* behind a `woof` verb.
- **LLM semantic extraction of code at index time** (graphify/potpie). It overlaps and would
  duplicate Woof's existing AS-IS prose mappers. Keep prose mapping in markdown; keep the
  structural graph deterministic.
- **CodeQL / build-aware whole-program databases.** Cost/latency is the opposite of Woof's goal.
- **Vendoring GitNexus or gortex code** - licence risk (PolyForm Noncommercial / ambiguous).
  Ideas only.

## Incidental finding (hygiene regression)

Woof dropped GSD's `scan_for_secrets` pre-commit gate and the mapper's `<forbidden_files>`
guard when forking `map-codebase` (`skills/woof/references/map-codebase.md` has neither). The
AS-IS docs are committed (ADR-004), so a mapper can surface a secret into a committed doc with
no scan. Cheap to re-add, independent of everything above. Backlog candidate.

## Decisions now recorded

1. Direction: accepted in ADR-009. Add a structural mechanical artefact and `woof cartography`
   verbs.
2. Storage: ADR-009 defaults to SQLite under `.woof/codebase/structural/`.
3. Scope: Python-only v1, after auditing any concurrent tree-sitter/parser work.
4. Semantic retrieval: separate E14 epic, not bundled into the first structural index.
5. Onboarding pass: separate E15 epic, started when a real large-repo onboarding target justifies it.

## Spike validation (2026-06-07)

Pre-build spikes on Woof's own source confirmed the plan empirically; see
`~/Work/vault/records/radianit/projects/woof/research/code-mapping/spikes/2026-06-07-extractor-storage-eval.md`
(write-up plus the prototype harness that seeds the E12 eval). Headlines:

- Extractor: tree-sitter and stdlib `ast` produce identical Python output (symbol Jaccard
  1.0, identical call resolution), tree-sitter marginally faster at ~30 LOC more. tree-sitter
  is the v1 mechanism (Python-only, additive via `languages/*.toml`); `ast` is a safe fallback.
- Storage: FTS5 ships in stdlib `sqlite3`; a 17.4k-LOC repo indexes to a 660 KiB db; all
  queries (callers/callees/impact via recursive CTE, search via FTS5) run in microseconds.
  SQLite confirmed; JSONL's only edge (diff-friendliness) is moot for a gitignored artefact.
- Advisory by necessity: only ~36% of call sites resolve internally; the dominant gap is
  `obj.method()` dispatch (2409 sites), an LSP concern not a parser one. On a hand-verified
  sample every `EXTRACTED` edge was correct, while bare-name `HEURISTIC` matching produced the
  predicted `list.append() -> _BoundedCapture.append` false positives - the evidence behind
  the confidence tiers and the precision-over-completeness rule.

# Discovery playbooks

Stage-1 (Discovery) prompt content for the Woof graph.

## Graph-owned producer-node prompts

Stage 1 runs four deterministic producer nodes in order. The graph returns the
next dispatch contract; the skill performs the producer dispatch:

- `research.md` - `discovery_research` node. Produces the `discovery/research/`
  bucket.
- `thinking.md` - `discovery_thinking` node. Produces the `discovery/thinking/`
  bucket.
- `brainstorm.md` - `discovery_brainstorm` node. Produces the
  `discovery/brainstorm/` bucket.
- `synthesis.md` - `discovery_synthesis` node. Reads the bucket artefacts and
  produces `discovery/synthesis/{CONCEPT,PRINCIPLES,ARCHITECTURE,OPEN_QUESTIONS}.md`.

`definition.md` is the graph-owned Stage 2 Definition producer-node prompt. The
Stage 3 Breakdown producer prompt lives under `playbooks/planning/`. These
prompts describe node-local output contracts only; the Python graph owns
successor selection.

## Building-block playbooks

- `research/` - eight research angles: history (prior attempts), feasibility
  (constraints), options (compare alternatives), technical (how-to), deep-dive
  (full investigation), competitive (other players), landscape (the space),
  open-source (existing tools).
- `consider/` - twelve thinking lenses for stress-testing a framing: 10-10-10,
  5-whys, Eisenhower matrix, first-principles, inversion, Occam's razor,
  one-thing, opportunity-cost, Pareto, second-order, SWOT, via-negativa.

These are *building blocks*, not a fixed sequence. The graph contract bundles every
playbook for a bucket into that bucket's producer prompt; the producer role
selects the playbooks that match the spark and the uncertainties in play. The
playbook text is embedded directly in the dispatched prompt, so an installed
Woof package still provides the full technique set. Each building-block playbook
is non-interactive: it carries `type: discovery-playbook` frontmatter, does not
use interactive question tools, and writes its artefact into the epic's
discovery bucket directory.

`ask-me-questions.md` is a human-operator intake aid for shaping a vague spark
before `/woof:run` creates an epic. It is interactive by design and is not a
graph-dispatched producer prompt.

Output of Stage 1 is `discovery/synthesis/{CONCEPT,PRINCIPLES,ARCHITECTURE,OPEN_QUESTIONS}.md`
per the EPIC schema's required pre-Definition state.

## Origin

Ported from [taches-cc-resources](https://github.com/lex-christopherson/taches-cc-resources)
(MIT, (c) 2025 Lex Christopherson). See the repo's `ACKNOWLEDGEMENTS.md` for the
full attribution.

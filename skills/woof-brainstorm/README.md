<!-- VENDORED from agent-toolkit skills/brainstorm - do not edit here. Regenerate with `just gen-brainstorm`. -->

# brainstorm

A skill for interactive design discovery. It turns a rough idea into a design that the human and
the agent both understand and agree on, then hands a schema-validated artefact to whatever builds
the work (a Woof epic or a plain document).

It is built on Domain-Driven Design and hexagonal architecture, applied as the agent's way of
thinking rather than as vocabulary imposed on the user.

## Why this exists

Most design conversations with an agent either jump to code too early or produce vague prose that
nothing downstream can use. This skill addresses both ends:

- It does not start building until there is a design the human has approved.
- It produces a structured, schema-validated artefact with precise terminology, explicit
  boundaries, and recorded decisions, so the next tool has something concrete to consume.

The design conversation is where most of the leverage in a software process sits, and it is
usually the least deliberate step. This skill makes it deliberate and repeatable.

## The two loops

Discovery is one activity run as two interactive loops, with a schema-validated contract between
them. Build the design up, then tear it down.

```
INPUTS
  spark/idea  +  blank tiered architecture template  +  (brownfield) existing code & CONTEXT.md
        |
        v
+- LOOP 1 . BRAINSTORM  (you lead . generative) -------------------+
|  research -> think -> ideate -> synthesise - open loop until done |
|  DDD/hex-lensed, plain-language, proportional elicitation         |
|  fills the architecture/design template at the right tier         |
+-------------------------------------------------------------------+
        |
   === CONTRACT 1 ===  architecture/design doc  . schema-validated . guardrailed
        |
        v
+- LOOP 2 . GRILL ME  (agent leads . critical) --------------------+
|  interview one-at-a-time . explore code first . challenge         |
|  terminology . stress-test scenarios . cross-reference code .     |
|  capture inline . ADRs selectively - own loop until resolution    |
|  back-edge: may reject and re-enter Loop 1 ----------------------->
+-------------------------------------------------------------------+
        |
   === CONTRACT 2 ===  resolved bundle: architecture doc + CONTEXT.md + ADRs + open-questions
        |
        v
   handoff -> a Woof epic  /  keep the bundle
```

### Loop 1 - Brainstorm (generative; you lead)

The agent researches, thinks, generates ideas, and synthesises a design, then presents it. You
discount, add, and redirect; the agent re-synthesises. The loop is open and runs until the design
is complete, however long that takes. There is no cap on options or turns; the depth scales with
how hard the problem is. You lead this loop, and your feedback keeps sparking new directions until
the shape settles.

### Loop 2 - Grill Me (critical; you are questioned)

Now the agent leads. It interrogates the design, and where code exists the code: one question at a
time, exploring the codebase before asking, challenging loose terminology, stress-testing with
concrete scenarios, and surfacing contradictions. It captures resolved terms into a glossary as it
goes and records the genuinely hard decisions as ADRs. The loop runs until the human and the agent
share a clear, consistent understanding. If grilling shows the design is wrong at the root, it
sends you back to Loop 1.

## DDD and hexagonal architecture as the lens

The agent thinks in bounded contexts, ownership, ports and adapters, dependency direction,
invariants, and ubiquitous language. It does not make you learn any of those terms. It asks plain
questions whose answers happen to have that shape:

| The agent is probing for | It asks you |
|---|---|
| Bounded context / module seam | "What are the distinct parts, and where would you draw the lines between them?" |
| Aggregate / ownership | "Which part owns this data? Who is allowed to change it?" |
| Ubiquitous language | "What exactly do you call this, and what do you specifically not mean by that word?" |
| Ports and adapters | "What does this part need from outside, and what sits on the other side of that line?" |
| Dependency direction | "If this changes, what else has to change? Which way does the arrow point?" |
| Invariants | "What must always be true here, no matter what?" |
| Change axes | "What tends to change together, and what changes on its own clock?" |

Speaking the domain's language rather than the framework's is itself the ubiquitous-language
principle. The framework stays in the agent's head; the conversation stays in your problem.

## The artefacts

The stage produces a bundle:

- An architecture or design document, structured by a tiered template (`TEMPLATE.md`). The tier
  scales with the problem, from a light decision note for a small change up to a full system
  architecture.
- `CONTEXT.md`, an opinionated glossary that is the single home for the project's terminology
  (`CONTEXT-FORMAT.md`).
- ADRs, recorded only for decisions that are hard to reverse, surprising without context, and the
  result of real trade-offs (`ADR-FORMAT.md`).
- A list of open questions carried forward.

## Greenfield and brownfield

One pipeline covers both. Loop 1 produces the design artefact that Loop 2 grills, so there is
always something to grill. Only the evidence differs: on an existing codebase the grilling
cross-references real code; on a blank slate it cross-references the design against your stated
intent.

## How the output is used

The bundle is deliberately neutral about who consumes it:

- A Woof epic decomposes it into an `EPIC.md` and a story plan.
- For research or a one-off decision, the bundle is the deliverable.

## Contracts and language

Every boundary is a JSON Schema over a markdown document with YAML front matter. The schema is
language-neutral data, and each consumer validates it natively. The skill ships documents and
schemas, not a runtime; where helper code is needed it is Python.

## Lineage

This skill consolidates prior work rather than reinventing it. See `ACKNOWLEDGEMENTS.md`.

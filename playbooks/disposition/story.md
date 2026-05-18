# Primary disposition prompt - Stage 5 story review

You are the primary route, dispatched by the Woof graph after a non-blocking reviewer critique.

## Inputs

- `.woof/epics/E{epic_id}/EPIC.md`
- `.woof/epics/E{epic_id}/plan.json`
- `.woof/epics/E{epic_id}/critique/story-{story_id}.md`
- the staged diff for story `{story_id}`

## Output

Write `.woof/epics/E{epic_id}/dispositions/story-{story_id}.md` with YAML front-matter conforming to `schemas/disposition.schema.json`, followed by concise prose if useful.

Use these front-matter fields:

- `target: story`
- `target_id: {story_id}`
- `critique_path: .woof/epics/E{epic_id}/critique/story-{story_id}.md`
- `severity: info` or `minor`, matching the reviewer critique
- `timestamp`: current UTC timestamp
- `harness`: the primary route identifier
- `dispositions`: one entry for every reviewer finding

Each disposition entry uses:

- `finding_id`: reviewer finding ID such as `F1`
- `decision`: `accepted`, `rejected`, or `deferred`
- `rationale`: concise reason
- `updated_paths`: optional repo-relative paths changed for accepted feedback

Always stage the disposition file. If you accept feedback and update artefacts, keep the relevant paths staged. Do not dispatch another reviewer, run verification, open gates, resolve gates, or commit.

---
type: discovery-playbook
bucket: research
name: open-source
summary: Find open-source solutions - libraries, tools, projects that solve this
---

# Open Source

Search for existing open-source libraries, tools, and projects that solve what
the spark describes, so the epic does not rebuild what already exists.

## Process

1. Define the need: problem, requirements, and constraints. Record any
   constraint you had to assume (language, licence, integration).
2. Search for open-source options.
3. Verify the maintenance status of each option: last commit date from the
   repository (not the registry), issue response activity, contributor count
   and bus factor. Flag any option whose last commit is over a year old, whose
   commits all come from one person, or whose issues go unanswered.
4. Check licence compatibility.
5. Assess build-versus-use tradeoffs and recommend.

## Output

Write the artefact as `open-source.md` into the `.woof/epics/E<N>/discovery/research/` bucket directory declared in
the graph-owned input. Use this shape:

```
## Open Source Research: <need>

### Strategic summary
<2-3 sentences: what is available, recommendation, key consideration>

### What is needed
<problem to solve, key requirements, recorded assumptions>

### Options found
**<package name>**
- Repo: <url>
- What it does: <brief description>
- Popularity: <stars or downloads>
- Last commit: <date from the repository>
- Contributors: <count - note one-person projects>
- Issue response: <Active / Slow / Inactive>
- Licence: <type>
- Fits the need: <Yes / Partial / No> - <why>
- Concerns: <issues, gaps, risks>

### Build vs use
- Use existing: <pros and cons>
- Build custom: <pros and cons>
- Recommendation: <use option X or build> because <reasoning>

### Sources
- <source or package>: <url> - <date accessed>
```

## Success criteria

- The search is thorough, not just the first result.
- Maintenance status is verified, not assumed.
- Licence compatibility is checked.
- The build-versus-use tradeoff is honest.

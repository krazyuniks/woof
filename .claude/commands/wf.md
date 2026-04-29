---
description: Thin wrapper around Woof's deterministic Python graph.
allowed-tools: Bash(woof:*), Bash(./bin/woof:*), Bash(./woof/bin/woof:*), Bash(just:*)
argument-hint: "--epic <N> [--once]"
---

# /wf

Invoke the deterministic graph. Do not inspect `.woof/` state yourself, select successors, dispatch subprocesses, write gates, or commit.

Run one of these, depending on checkout layout:

```bash
woof wf $ARGUMENTS
```

If `woof` is not on PATH, use:

```bash
./bin/woof wf $ARGUMENTS
```

In a consumer repo with Woof vendored or submoduled at `woof/`, use:

```bash
./woof/bin/woof wf $ARGUMENTS
```

Report the command output only. The Python graph is the orchestrator.

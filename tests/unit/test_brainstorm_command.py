"""Tests for `woof brainstorm` - interactive Stage-0 bundle ingestion."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from woof.graph.state import NodeType
from woof.graph.transitions import next_node

if TYPE_CHECKING:
    import subprocess
    from collections.abc import Callable

    RunWoof = Callable[..., subprocess.CompletedProcess[str]]

VALID_BUNDLE = """---
title: Backlog runner
tier: feature
mode: sdlc
status: accepted
context_ref: CONTEXT.md
work_units:
  - id: WU1
    title: Parse task front-matter
    summary: Read YAML and expose the task status.
    bounded_context: task-store
    acceptance:
      - A malformed task is rejected with a clear message.
  - id: WU2
    title: Drive one iteration
    summary: Dispatch, parse the status block, checkpoint.
    bounded_context: runner
    acceptance:
      - A done status triggers verify.
    deps: [WU1]
---

## Problem and intent
Drain a queue without babysitting.
"""

CONTEXT = "# Backlog runner context\n\n**Task**\nA unit of work. Avoid: job.\n"


def _make_project(tmp_path: Path) -> Path:
    (tmp_path / ".woof" / "epics").mkdir(parents=True)
    return tmp_path


def test_brainstorm_ingests_bundle_and_skips_headless_chain(
    tmp_path: Path, run_woof: RunWoof
) -> None:
    project = _make_project(tmp_path)
    bundle = project / "bundle.md"
    bundle.write_text(VALID_BUNDLE, encoding="utf-8")
    (project / "CONTEXT.md").write_text(CONTEXT, encoding="utf-8")

    result = run_woof(
        "brainstorm", "--epic", "1", "--from-bundle", str(bundle), "--project-root", str(project)
    )

    assert result.returncode == 0, result.stderr
    bucket = project / ".woof" / "epics" / "E1" / "discovery" / "brainstorm"
    assert (bucket / "design.md").is_file()
    assert (bucket / "CONTEXT.md").is_file()
    assert (project / ".woof" / "epics" / "E1" / "spark.md").is_file()
    events = (project / ".woof" / "epics" / "E1" / "epic.jsonl").read_text(encoding="utf-8")
    assert '"event":"discovery_bucket_explored"' in events
    assert '"bucket":"brainstorm"' in events

    # The interactive bucket stands in for the headless chain.
    assert next_node(project, 1) == (NodeType.DISCOVERY_SYNTHESIS, None)


def test_brainstorm_refuses_rejected_bundle(tmp_path: Path, run_woof: RunWoof) -> None:
    project = _make_project(tmp_path)
    bundle = project / "rejected.md"
    bundle.write_text(
        "---\ntitle: X\ntier: feature\nstatus: rejected\nwork_units: []\n"
        "rejection:\n  reason: root flaw\n---\n",
        encoding="utf-8",
    )
    result = run_woof(
        "brainstorm", "--epic", "2", "--from-bundle", str(bundle), "--project-root", str(project)
    )
    assert result.returncode != 0
    assert "rejected" in result.stderr
    assert not (project / ".woof" / "epics" / "E2").exists()


def test_brainstorm_refuses_invalid_bundle(tmp_path: Path, run_woof: RunWoof) -> None:
    project = _make_project(tmp_path)
    bundle = project / "bad.md"
    bundle.write_text(
        "---\ntitle: X\ntier: feature\nstatus: accepted\nwork_units:\n"
        "  - id: bad\n    title: t\n    summary: s\n    bounded_context: c\n    acceptance: [a]\n---\n",
        encoding="utf-8",
    )
    result = run_woof(
        "brainstorm", "--epic", "3", "--from-bundle", str(bundle), "--project-root", str(project)
    )
    assert result.returncode != 0
    assert not (project / ".woof" / "epics" / "E3").exists()

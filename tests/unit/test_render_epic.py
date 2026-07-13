"""Black-box tests for ``woof render-epic``.

Pure-render tests use no network. ``--sync`` tests substitute a stub ``gh``
binary on PATH.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

from tests.support import DEFAULT_PROJECT_KEY, seed_project_config
from woof import state

REPO_ROOT = Path(__file__).resolve().parents[2]
WOOF_BIN = REPO_ROOT / "bin" / "woof"


pytestmark = pytest.mark.host_only


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _epic_md(front: str, prose: str = "") -> str:
    return f"---\n{front}---\n{prose}"


def _plan_json(*, done: bool = False) -> str:
    status = "done" if done else "pending"
    return json.dumps(
        {
            "epic_id": 42,
            "goal": "Ship comment publishing.",
            "work_units": [
                {
                    "id": "S1",
                    "title": "Create comment API",
                    "summary": "Add the write API.",
                    "paths": ["src/comments.py"],
                    "satisfies": ["O1"],
                    "implements_contract_decisions": ["CD1"],
                    "uses_contract_decisions": [],
                    "deps": [],
                    "tests": {"count": 2, "types": ["unit"]},
                    "status": status,
                },
                {
                    "id": "S2",
                    "title": "Render live comments",
                    "summary": "Show new comments in real time.",
                    "paths": ["src/live.py"],
                    "satisfies": ["O2"],
                    "implements_contract_decisions": [],
                    "uses_contract_decisions": ["CD1", "CD2"],
                    "deps": ["S1"],
                    "tests": {"count": 1, "types": ["integration"]},
                    "status": status,
                },
            ],
        }
    )


def _write_last_sync(
    epic_project: Path,
    *,
    updated_at: str = "2026-01-01T00:00:00Z",
    body: str = "<previous>",
) -> None:
    state.last_sync_path(DEFAULT_PROJECT_KEY, 42).write_text(
        json.dumps(
            {
                "issue_number": 42,
                "updated_at": updated_at,
                "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                "body": body,
            }
        )
        + "\n"
    )


@pytest.fixture
def epic_project(tmp_path: Path) -> Path:
    """Skeleton git checkout with a GitHub-tracker project config and a sample EPIC.md."""
    project = tmp_path / "proj"
    project.mkdir(parents=True)
    epic_dir = state.epic_dir(DEFAULT_PROJECT_KEY, 42)
    epic_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    seed_project_config({"tracker": {"kind": "github", "repo": "acme/widgets"}})

    front = textwrap.dedent("""\
        epic_id: 42
        title: Comment publishing
        observable_outcomes:
          - id: O1
            statement: Users can post a comment.
            verification: automated
          - id: O2
            statement: Comments appear in real time.
            verification: hybrid
            deprecated: true
            replaced_by: O3
        contract_decisions:
          - id: CD1
            related_outcomes: [O1, O2]
            title: Comment publishing route
            openapi_ref: spec/openapi.yaml#/paths/~1api~1v1~1comments/post
          - id: CD2
            related_outcomes: [O1]
            title: Comment payload
            pydantic_ref: webapp/schemas/comment.py:CommentCreate
        acceptance_criteria:
          - All outcomes verified by tests in diff.
          - Contract decisions validate via native tooling.
        open_questions:
          - id: OQ1
            question: Should drafts be persisted server-side?
            deferral_reason: Needs product policy.
    """)
    prose = "Enable users to publish comments on shootouts.\n\nFurther context follows.\n"
    (epic_dir / "EPIC.md").write_text(_epic_md(front, prose))
    return project


def _run(
    project: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(WOOF_BIN), *args],
        capture_output=True,
        text=True,
        cwd=project,
        env=env,
    )


# ---------------------------------------------------------------------------
# pure render
# ---------------------------------------------------------------------------


def test_render_full_body(epic_project: Path) -> None:
    proc = _run(epic_project, "render-epic", "--epic", "42")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == (
        "Enable users to publish comments on shootouts.\n\n"
        "## Observable Outcomes\n\n"
        "- **O1** — Users can post a comment.\n"
        "  - Verification: automated\n"
        "- **O2** — Comments appear in real time. _(deprecated → O3)_\n"
        "  - Verification: hybrid\n"
        "\n"
        "## Contract Decisions\n\n"
        "| ID | Related Outcomes | Title | Contract Reference |\n"
        "|---|---|---|---|\n"
        "| CD1 | O1, O2 | Comment publishing route | "
        "`openapi: spec/openapi.yaml#/paths/~1api~1v1~1comments/post` |\n"
        "| CD2 | O1 | Comment payload | "
        "`pydantic: webapp/schemas/comment.py:CommentCreate` |\n"
        "\n"
        "## Acceptance Criteria\n\n"
        "- All outcomes verified by tests in diff.\n"
        "- Contract decisions validate via native tooling.\n"
        "\n"
        "## Open Questions\n\n"
        "- **OQ1** — Should drafts be persisted server-side? (Deferred: Needs product policy.)\n"
        "\n"
        "---\n\n"
        "<!-- woof — structured sections above are rewritten on Definition/plan changes. "
        "Free-form prose above `## Observable Outcomes` is preserved on overwrite. "
        "Do not edit structured sections directly in the issue tracker. -->\n"
    )


def test_render_uses_front_matter_intent_before_body_prose(epic_project: Path) -> None:
    epic_md = state.epic_dir(DEFAULT_PROJECT_KEY, 42) / "EPIC.md"
    text = epic_md.read_text()
    epic_md.write_text(
        text.replace(
            "title: Comment publishing\n",
            "title: Comment publishing\nintent: Canonical GitHub intent.\n",
        )
    )

    proc = _run(epic_project, "render-epic", "--epic", "42")

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("Canonical GitHub intent.\n\n## Observable Outcomes")


def test_render_to_output_file(epic_project: Path, tmp_path: Path) -> None:
    out = tmp_path / "rendered.md"
    proc = _run(epic_project, "render-epic", "--epic", "42", "--output", str(out))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""
    assert out.read_text().startswith("Enable users to publish comments on shootouts.")


def test_missing_epic_md(epic_project: Path) -> None:
    proc = _run(epic_project, "render-epic", "--epic", "999")
    assert proc.returncode == 2
    assert "EPIC.md not found" in proc.stderr


def test_invalid_front_matter(tmp_path: Path) -> None:
    project = tmp_path / "p"
    project.mkdir(parents=True)
    epic_dir = state.epic_dir(DEFAULT_PROJECT_KEY, 1)
    epic_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    seed_project_config({"tracker": {"kind": "github", "repo": "x/y"}})
    # Missing required acceptance_criteria
    (epic_dir / "EPIC.md").write_text(
        _epic_md(
            "epic_id: 1\ntitle: T\nobservable_outcomes: [{id: O1, statement: x, verification: automated}]\ncontract_decisions: []\n",
            "summary",
        )
    )
    proc = _run(project, "render-epic", "--epic", "1")
    assert proc.returncode == 2
    assert "front-matter invalid" in proc.stderr


# ---------------------------------------------------------------------------
# preservation rule
# ---------------------------------------------------------------------------


def test_preservation_uses_remote_prefix_when_marker_present(
    epic_project: Path, tmp_path: Path
) -> None:
    """When the remote body already contains the structured marker, the prose
    above it is preserved on rewrite (not the EPIC.md prose)."""
    bin_dir = tmp_path / "bin"
    remote = {
        "updated_at": "2026-01-01T00:00:00Z",
        "body": (
            "Hand-edited intro paragraph from a teammate.\n\n"
            "## Observable Outcomes\n\n- (will be overwritten)\n"
        ),
    }
    _make_gh_stub(bin_dir, fetch_payload=remote)
    env = _stub_env(bin_dir)

    proc = _run(epic_project, "render-epic", "--epic", "42", "--sync", env=env)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert out.startswith("Hand-edited intro paragraph from a teammate.\n\n## Observable Outcomes")
    # The EPIC.md intent paragraph must NOT appear at the top
    assert not out.startswith("Enable users")


def test_preservation_requires_managed_heading_line(epic_project: Path, tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    remote = {
        "updated_at": "2026-01-01T00:00:00Z",
        "body": "Teammate wrote about ## Observable Outcomes inline, not as a heading.\n",
    }
    _make_gh_stub(bin_dir, fetch_payload=remote)

    proc = _run(epic_project, "render-epic", "--epic", "42", "--sync", env=_stub_env(bin_dir))

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("Enable users to publish comments on shootouts.\n\n")


# ---------------------------------------------------------------------------
# --sync stub helpers
# ---------------------------------------------------------------------------


def _make_gh_stub(
    bin_dir: Path,
    fetch_payload: dict,
    fetch_payload_after_edit: dict | None = None,
    fetch_payload_after_close: dict | None = None,
) -> None:
    """Write an executable stub ``gh`` that returns canned JSON for ``api``
    calls and accepts ``issue edit``. ``fetch_payload`` is returned until an
    ``issue edit`` happens; thereafter ``fetch_payload_after_edit`` is returned.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    last_body = bin_dir / "_last_body"
    closed = bin_dir / "_closed"
    if fetch_payload_after_edit is None:
        fetch_payload_after_edit = fetch_payload
    if fetch_payload_after_close is None:
        fetch_payload_after_close = fetch_payload_after_edit
    before = json.dumps(fetch_payload)
    after = json.dumps(fetch_payload_after_edit)
    after_close = json.dumps(fetch_payload_after_close)
    script = bin_dir / "gh"
    # Direct write — no dedent, no heredoc, to keep the shebang at column 0.
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'mode="$1"; shift\n'
        'case "$mode" in\n'
        "  api)\n"
        f'    if [[ -f "{closed}" ]]; then\n'
        f"      printf '%s' '{after_close}'\n"
        f'    elif [[ -f "{last_body}" ]]; then\n'
        f"      printf '%s' '{after}'\n"
        "    else\n"
        f"      printf '%s' '{before}'\n"
        "    fi\n"
        "    ;;\n"
        "  issue)\n"
        '    sub="$1"; shift\n'
        '    case "$sub" in\n'
        "      edit)\n"
        '    body_file=""\n'
        "    while [[ $# -gt 0 ]]; do\n"
        '      case "$1" in\n'
        '        --body-file) body_file="$2"; shift 2;;\n'
        "        *) shift;;\n"
        "      esac\n"
        "    done\n"
        '    if [[ "$body_file" == "-" ]]; then\n'
        f'      cat > "{last_body}"\n'
        '    elif [[ -n "$body_file" ]]; then\n'
        f'      cp "$body_file" "{last_body}"\n'
        "    fi\n"
        "        ;;\n"
        "      close)\n"
        f'        printf "closed\\n" > "{closed}"\n'
        "        ;;\n"
        "      *)\n"
        '        echo "stub gh: unsupported issue subcommand" >&2\n'
        "        exit 2\n"
        "        ;;\n"
        "    esac\n"
        "    ;;\n"
        "  *)\n"
        '    echo "stub gh: unsupported mode" >&2\n'
        "    exit 2\n"
        "    ;;\n"
        "esac\n"
    )
    script.chmod(0o755)


def _stub_env(bin_dir: Path) -> dict[str, str]:
    """Put the stub ``gh`` first on PATH, keeping WOOF_HOME/WOOF_PROJECT inherited."""
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    return env


# ---------------------------------------------------------------------------
# --sync: clean push
# ---------------------------------------------------------------------------


def test_sync_first_push_writes_last_sync(epic_project: Path, tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    _make_gh_stub(
        bin_dir,
        fetch_payload={"updated_at": "2026-01-01T00:00:00Z", "body": ""},
        fetch_payload_after_edit={"updated_at": "2026-01-02T12:34:56Z", "body": "<post-push>"},
    )
    proc = _run(epic_project, "render-epic", "--epic", "42", "--sync", env=_stub_env(bin_dir))
    assert proc.returncode == 0, proc.stderr

    last_sync_path = state.epic_dir(DEFAULT_PROJECT_KEY, 42) / ".last-sync"
    last_sync = json.loads(last_sync_path.read_text())
    assert last_sync["issue_number"] == 42
    assert last_sync["updated_at"] == "2026-01-02T12:34:56Z"
    assert len(last_sync["body_sha256"]) == 64

    jsonl = (state.epic_dir(DEFAULT_PROJECT_KEY, 42) / "epic.jsonl").read_text()
    events = [json.loads(ln) for ln in jsonl.splitlines() if ln.strip()]
    assert any(e["event"] == "tracker_synced" for e in events)


def test_wf_plan_gate_approval_syncs_plan_summary(epic_project: Path, tmp_path: Path) -> None:
    epic_dir = state.epic_dir(DEFAULT_PROJECT_KEY, 42)
    (epic_dir / "plan.json").write_text(_plan_json())
    (epic_dir / "gate.md").write_text(
        "---\ntype: plan_gate\nstage: 4\nwork_unit_id: null\ntriggered_by: [plan_review]\n---\n"
    )
    remote_body = "Remote intent.\n\n## Observable Outcomes\n\n- stale\n"
    _write_last_sync(epic_project, body=remote_body)

    bin_dir = tmp_path / "bin"
    _make_gh_stub(
        bin_dir,
        fetch_payload={
            "updated_at": "2026-01-01T00:00:00Z",
            "body": remote_body,
        },
        fetch_payload_after_edit={
            "updated_at": "2026-01-02T00:00:00Z",
            "body": "<post-plan-sync>",
        },
    )

    proc = _run(epic_project, "wf", "--epic", "42", "--resolve", "approve", env=_stub_env(bin_dir))

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "woof wf: gate resolved decision=approve\n"
    assert not (epic_dir / "gate.md").exists()
    pushed_body = (bin_dir / "_last_body").read_text()
    assert "## Plan Summary\n\n" in pushed_body
    assert "- **S1** — Create comment API\n" in pushed_body
    assert "- **S2** — Render live comments\n" in pushed_body
    assert "## Closing Summary" not in pushed_body
    assert not (bin_dir / "_closed").exists()
    last_sync = json.loads((epic_dir / ".last-sync").read_text())
    assert last_sync["updated_at"] == "2026-01-02T00:00:00Z"


def test_wf_epic_completion_syncs_closing_summary_and_closes_issue(
    epic_project: Path, tmp_path: Path
) -> None:
    epic_dir = state.epic_dir(DEFAULT_PROJECT_KEY, 42)
    (epic_dir / "plan.json").write_text(_plan_json(done=True))
    remote_body = "Remote intent.\n\n## Observable Outcomes\n\n- stale\n"
    _write_last_sync(epic_project, body=remote_body)

    bin_dir = tmp_path / "bin"
    _make_gh_stub(
        bin_dir,
        fetch_payload={
            "updated_at": "2026-01-01T00:00:00Z",
            "body": remote_body,
            "state": "open",
        },
        fetch_payload_after_edit={
            "updated_at": "2026-01-02T00:00:00Z",
            "body": "<post-completion-sync>",
            "state": "open",
        },
        fetch_payload_after_close={
            "updated_at": "2026-01-03T00:00:00Z",
            "body": "<post-close>",
            "state": "closed",
        },
    )

    proc = _run(epic_project, "wf", "--epic", "42", env=_stub_env(bin_dir))

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "woof wf: human_review -> epic_complete: E42 complete\n"
    pushed_body = (bin_dir / "_last_body").read_text()
    assert "## Plan Summary\n\n" in pushed_body
    assert (
        "## Closing Summary\n\nEpic completed with 2/2 planned work units done.\n\n" in pushed_body
    )
    assert (bin_dir / "_closed").exists()
    last_sync = json.loads((epic_dir / ".last-sync").read_text())
    assert last_sync["updated_at"] == "2026-01-03T00:00:00Z"
    events = [
        json.loads(line) for line in (epic_dir / "epic.jsonl").read_text().splitlines() if line
    ]
    assert any(event["event"] == "tracker_synced" for event in events)
    assert any(event["event"] == "epic_completed" for event in events)


# ---------------------------------------------------------------------------
# --sync: conflict
# ---------------------------------------------------------------------------


def test_sync_conflict_detected(epic_project: Path, tmp_path: Path) -> None:
    """If remote updated_at differs from .last-sync, push is aborted."""
    epic_dir = state.epic_dir(DEFAULT_PROJECT_KEY, 42)
    _write_last_sync(epic_project, updated_at="2025-12-01T00:00:00Z", body="<old>")

    bin_dir = tmp_path / "bin"
    _make_gh_stub(
        bin_dir,
        fetch_payload={"updated_at": "2026-04-01T00:00:00Z", "body": "remote diverged"},
    )
    proc = _run(epic_project, "render-epic", "--epic", "42", "--sync", env=_stub_env(bin_dir))
    assert proc.returncode == 3
    assert "tracker_sync_conflict" in proc.stderr
    # No push happened — last_body marker absent
    assert not (bin_dir / "_last_body").exists()
    gate = epic_dir / "gate.md"
    assert gate.exists()
    gate_text = gate.read_text()
    gate_front = yaml.safe_load(gate_text[4 : gate_text.find("\n---\n", 4)])
    assert gate_front["type"] == "plan_gate"
    assert gate_front["work_unit_id"] is None
    assert gate_front["triggered_by"] == ["tracker_sync_conflict"]
    assert "### Diff: last-pushed -> current remote" in gate_text
    assert "-<old>" in gate_text
    assert "+remote diverged" in gate_text
    assert "### Diff: last-pushed -> current local render" in gate_text
    assert "+## Observable Outcomes" in gate_text
    # last-sync untouched
    last_sync = json.loads((epic_dir / ".last-sync").read_text())
    assert last_sync["updated_at"] == "2025-12-01T00:00:00Z"

    events = [
        json.loads(ln) for ln in (epic_dir / "epic.jsonl").read_text().splitlines() if ln.strip()
    ]
    assert any(
        e["event"] == "plan_gate_opened" and e["triggered_by"] == ["tracker_sync_conflict"]
        for e in events
    )
    assert any(e["event"] == "tracker_sync_conflict" for e in events)


def test_sync_conflict_detected_when_remote_body_hash_diverges(
    epic_project: Path, tmp_path: Path
) -> None:
    """If remote body hash differs from .last-sync, push is aborted."""
    epic_dir = state.epic_dir(DEFAULT_PROJECT_KEY, 42)
    _write_last_sync(epic_project, updated_at="2026-04-01T00:00:00Z", body="<old>")

    bin_dir = tmp_path / "bin"
    _make_gh_stub(
        bin_dir,
        fetch_payload={"updated_at": "2026-04-01T00:00:00Z", "body": "remote diverged"},
    )
    proc = _run(epic_project, "render-epic", "--epic", "42", "--sync", env=_stub_env(bin_dir))

    assert proc.returncode == 3
    assert "body_sha256" in proc.stderr
    assert not (bin_dir / "_last_body").exists()
    assert (epic_dir / "gate.md").exists()


def test_wf_plan_gate_approval_opens_sync_conflict_gate(epic_project: Path, tmp_path: Path) -> None:
    epic_dir = state.epic_dir(DEFAULT_PROJECT_KEY, 42)
    (epic_dir / "plan.json").write_text(_plan_json())
    (epic_dir / "gate.md").write_text(
        "---\ntype: plan_gate\nstage: 4\nwork_unit_id: null\ntriggered_by: [plan_review]\n---\n"
    )
    _write_last_sync(epic_project, updated_at="2025-12-01T00:00:00Z", body="<old>")

    bin_dir = tmp_path / "bin"
    _make_gh_stub(
        bin_dir,
        fetch_payload={
            "updated_at": "2026-04-01T00:00:00Z",
            "body": "Remote intent.\n\n## Observable Outcomes\n\n- teammate edit\n",
        },
    )

    proc = _run(epic_project, "wf", "--epic", "42", "--resolve", "approve", env=_stub_env(bin_dir))

    assert proc.returncode == 2
    assert "tracker error: tracker_sync_conflict" in proc.stderr
    assert not (bin_dir / "_last_body").exists()
    gate_text = (epic_dir / "gate.md").read_text()
    gate_front = yaml.safe_load(gate_text[4 : gate_text.find("\n---\n", 4)])
    assert gate_front["triggered_by"] == ["tracker_sync_conflict"]
    assert "### Diff: last-pushed -> current remote" in gate_text
    assert "### Diff: last-pushed -> current local render" in gate_text


def test_wf_resolve_sync_conflict_keep_local_updates_last_sync_baseline(
    epic_project: Path, tmp_path: Path
) -> None:
    epic_dir = state.epic_dir(DEFAULT_PROJECT_KEY, 42)
    _write_last_sync(epic_project, updated_at="2025-12-01T00:00:00Z", body="<old>")
    (epic_dir / "gate.md").write_text(
        "---\n"
        "type: plan_gate\n"
        "stage: 4\n"
        "work_unit_id: null\n"
        "triggered_by: [tracker_sync_conflict]\n"
        "timestamp: '2026-01-01T00:00:00Z'\n"
        "---\n"
        "## Context\n\nConflict.\n\n"
        "## Findings\n\n- remote changed\n\n"
        "## Primary position\n\nKeep local.\n\n"
        "## Reviewer position\n\nUpdate baseline, then retry.\n"
    )

    bin_dir = tmp_path / "bin"
    remote_body = "Remote teammate edit.\n"
    _make_gh_stub(
        bin_dir,
        fetch_payload={"number": 42, "updated_at": "2026-04-01T00:00:00Z", "body": remote_body},
    )

    proc = _run(
        epic_project,
        "wf",
        "--epic",
        "42",
        "--resolve",
        "keep_local",
        env=_stub_env(bin_dir),
    )

    assert proc.returncode == 0, proc.stderr
    assert not (epic_dir / "gate.md").exists()
    assert not (bin_dir / "_last_body").exists()
    last_sync = json.loads((epic_dir / ".last-sync").read_text())
    assert last_sync["updated_at"] == "2026-04-01T00:00:00Z"
    assert last_sync["body"] == remote_body
    events = [
        json.loads(line) for line in (epic_dir / "epic.jsonl").read_text().splitlines() if line
    ]
    assert events[-1]["event"] == "gate_resolved"
    assert events[-1]["decision"] == "keep_local"
    assert events[-1]["triggered_by"] == ["tracker_sync_conflict"]


def test_wf_resolve_sync_conflict_accept_remote_updates_epic_md(
    epic_project: Path, tmp_path: Path
) -> None:
    epic_dir = state.epic_dir(DEFAULT_PROJECT_KEY, 42)
    _write_last_sync(epic_project, updated_at="2025-12-01T00:00:00Z", body="<old>")
    (epic_dir / "gate.md").write_text(
        "---\n"
        "type: plan_gate\n"
        "stage: 4\n"
        "work_unit_id: null\n"
        "triggered_by: [tracker_sync_conflict]\n"
        "timestamp: '2026-01-01T00:00:00Z'\n"
        "---\n"
        "## Context\n\nConflict.\n\n"
        "## Findings\n\n- remote changed\n\n"
        "## Primary position\n\nAccept remote.\n\n"
        "## Reviewer position\n\nMirror GitHub locally.\n"
    )
    remote_body = (
        "Remote canonical intent.\n\n"
        "## Observable Outcomes\n\n"
        "- **O1** - Remote outcome.\n"
        "  - Verification: manual\n\n"
        "## Acceptance Criteria\n\n"
        "- Remote criteria.\n\n"
        "---\n\n"
        "<!-- woof sentinel -->\n"
    )

    bin_dir = tmp_path / "bin"
    _make_gh_stub(
        bin_dir,
        fetch_payload={
            "number": 42,
            "title": "Remote title",
            "updated_at": "2026-04-01T00:00:00Z",
            "body": remote_body,
        },
    )

    proc = _run(
        epic_project,
        "wf",
        "--epic",
        "42",
        "--resolve",
        "accept_remote",
        env=_stub_env(bin_dir),
    )

    assert proc.returncode == 0, proc.stderr
    assert not (epic_dir / "gate.md").exists()
    front = yaml.safe_load(
        (epic_dir / "EPIC.md").read_text()[
            4 : (epic_dir / "EPIC.md").read_text().find("\n---\n", 4)
        ]
    )
    assert front["title"] == "Remote title"
    assert front["observable_outcomes"][0]["statement"] == "Remote outcome."
    assert front["acceptance_criteria"] == ["Remote criteria."]
    last_sync = json.loads((epic_dir / ".last-sync").read_text())
    assert last_sync["body"] == remote_body

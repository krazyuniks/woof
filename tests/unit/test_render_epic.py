"""Black-box tests for ``woof render-epic``.

Pure-render tests use no network. ``--sync`` tests substitute a stub ``gh``
binary on PATH.
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WOOF_BIN = REPO_ROOT / "bin" / "woof"


pytestmark = pytest.mark.host_only


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _epic_md(front: str, prose: str = "") -> str:
    return f"---\n{front}---\n{prose}"


@pytest.fixture
def epic_project(tmp_path: Path) -> Path:
    """Skeleton project with `.woof/prerequisites.toml` + a sample EPIC.md."""
    project = tmp_path / "proj"
    epic_dir = project / ".woof" / "epics" / "E42"
    epic_dir.mkdir(parents=True)

    (project / ".woof" / "prerequisites.toml").write_text(
        textwrap.dedent("""\
        [github]
        repo = "acme/widgets"
    """)
    )

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
          - Should drafts be persisted server-side?
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
    body = proc.stdout
    assert body.startswith("Enable users to publish comments on shootouts.\n\n")
    assert "## Observable Outcomes" in body
    assert "- **O1** — Users can post a comment." in body
    assert "  - Verification: automated" in body
    assert "_(deprecated → O3)_" in body  # O2 deprecation marker
    assert "## Contract Decisions" in body
    assert (
        "| CD1 | O1, O2 | Comment publishing route | `openapi: spec/openapi.yaml#/paths/~1api~1v1~1comments/post` |"
        in body
    )
    assert (
        "| CD2 | O1 | Comment payload | `pydantic: webapp/schemas/comment.py:CommentCreate` |"
        in body
    )
    assert "## Acceptance Criteria" in body
    assert "- All outcomes verified by tests in diff." in body
    assert "## Open Questions" in body
    assert "- Should drafts be persisted server-side?" in body
    assert body.rstrip().endswith("-->")
    assert "woof — structured sections above are rewritten" in body


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
    epic_dir = project / ".woof" / "epics" / "E1"
    epic_dir.mkdir(parents=True)
    (project / ".woof" / "prerequisites.toml").write_text('[github]\nrepo = "x/y"\n')
    # Missing required acceptance_criteria
    (epic_dir / "EPIC.md").write_text(
        _epic_md(
            "epic_id: 1\ntitle: T\nobservable_outcomes: [{id: O1, statement: x, verification: automated}]\ncontract_decisions: []\n",
            "intent",
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


# ---------------------------------------------------------------------------
# --sync stub helpers
# ---------------------------------------------------------------------------


def _make_gh_stub(
    bin_dir: Path,
    fetch_payload: dict,
    fetch_payload_after_edit: dict | None = None,
) -> None:
    """Write an executable stub ``gh`` that returns canned JSON for ``api``
    calls and accepts ``issue edit``. ``fetch_payload`` is returned until an
    ``issue edit`` happens; thereafter ``fetch_payload_after_edit`` is returned.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    last_body = bin_dir / "_last_body"
    if fetch_payload_after_edit is None:
        fetch_payload_after_edit = fetch_payload
    before = json.dumps(fetch_payload)
    after = json.dumps(fetch_payload_after_edit)
    script = bin_dir / "gh"
    # Direct write — no dedent, no heredoc, to keep the shebang at column 0.
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'mode="$1"; shift\n'
        'case "$mode" in\n'
        "  api)\n"
        f'    if [[ -f "{last_body}" ]]; then\n'
        f"      printf '%s' '{after}'\n"
        "    else\n"
        f"      printf '%s' '{before}'\n"
        "    fi\n"
        "    ;;\n"
        "  issue)\n"
        '    body_file=""\n'
        "    while [[ $# -gt 0 ]]; do\n"
        '      case "$1" in\n'
        '        --body-file) body_file="$2"; shift 2;;\n'
        "        *) shift;;\n"
        "      esac\n"
        "    done\n"
        '    if [[ -n "$body_file" ]]; then\n'
        f'      cp "$body_file" "{last_body}"\n'
        "    fi\n"
        "    ;;\n"
        "  *)\n"
        '    echo "stub gh: unsupported mode" >&2\n'
        "    exit 2\n"
        "    ;;\n"
        "esac\n"
    )
    script.chmod(0o755)


def _stub_env(bin_dir: Path) -> dict[str, str]:
    return {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": os.environ.get("HOME", "/tmp"),
    }


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

    last_sync_path = epic_project / ".woof" / "epics" / "E42" / ".last-sync"
    last_sync = json.loads(last_sync_path.read_text())
    assert last_sync["issue_number"] == 42
    assert last_sync["updated_at"] == "2026-01-02T12:34:56Z"
    assert len(last_sync["body_sha256"]) == 64

    jsonl = (epic_project / ".woof" / "epics" / "E42" / "epic.jsonl").read_text()
    events = [json.loads(ln) for ln in jsonl.splitlines() if ln.strip()]
    assert any(e["event"] == "github_synced" for e in events)


# ---------------------------------------------------------------------------
# --sync: conflict
# ---------------------------------------------------------------------------


def test_sync_conflict_detected(epic_project: Path, tmp_path: Path) -> None:
    """If remote updated_at differs from .last-sync, push is aborted."""
    epic_dir = epic_project / ".woof" / "epics" / "E42"
    (epic_dir / ".last-sync").write_text(
        json.dumps(
            {
                "issue_number": 42,
                "updated_at": "2025-12-01T00:00:00Z",  # stale
                "body_sha256": "0" * 64,
                "body": "<old>",
            }
        )
    )

    bin_dir = tmp_path / "bin"
    _make_gh_stub(
        bin_dir,
        fetch_payload={"updated_at": "2026-04-01T00:00:00Z", "body": "remote diverged"},
    )
    proc = _run(epic_project, "render-epic", "--epic", "42", "--sync", env=_stub_env(bin_dir))
    assert proc.returncode == 3
    assert "github_sync_conflict" in proc.stderr
    # No push happened — last_body marker absent
    assert not (bin_dir / "_last_body").exists()
    # last-sync untouched
    last_sync = json.loads((epic_dir / ".last-sync").read_text())
    assert last_sync["updated_at"] == "2025-12-01T00:00:00Z"

    events = [
        json.loads(ln) for ln in (epic_dir / "epic.jsonl").read_text().splitlines() if ln.strip()
    ]
    assert any(e["event"] == "github_sync_conflict" for e in events)

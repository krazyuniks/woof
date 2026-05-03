from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WOOF_BIN = REPO_ROOT / "bin" / "woof"


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".woof").mkdir(parents=True)
    (project / ".woof" / "prerequisites.toml").write_text('[github]\nrepo = "acme/widgets"\n')
    return project


def _run(
    project: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(WOOF_BIN), *args],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
    )


def _stub_env(bin_dir: Path) -> dict[str, str]:
    return {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": os.environ.get("HOME", "/tmp"),
    }


def _make_gh_stub(bin_dir: Path, payload: dict | None = None, *, fail: bool = False) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "gh"
    if fail:
        script.write_text("#!/usr/bin/env bash\necho 'HTTP 404: Not Found' >&2\nexit 1\n")
    else:
        assert payload is not None
        script.write_text(
            f"#!/usr/bin/env bash\nset -euo pipefail\nprintf '%s' '{json.dumps(payload)}'\n"
        )
    script.chmod(0o755)


def _structured_body() -> str:
    return textwrap.dedent(
        """\
        Preserve this teammate-written intent.

        ## Observable Outcomes

        - **O1** — Users can post a comment.
          - Verification: automated
        - **O2** — Comments appear in real time. _(deprecated → O3)_
          - Verification: hybrid

        ## Contract Decisions

        | ID | Related Outcomes | Title | Contract Reference |
        |---|---|---|---|
        | CD1 | O1, O2 | Comment route | `openapi: spec/openapi.yaml#/paths/~1comments/post` |
        | CD2 | O1 | Comment payload _(deprecated → CD3)_ | `pydantic: webapp/schemas/comment.py:CommentCreate` |

        ## Acceptance Criteria

        - All outcomes verified by tests in diff.
        - Contract decisions validate via native tooling.

        ## Open Questions

        - Should drafts be persisted server-side?

        ---

        <!-- woof sentinel -->
        """
    )


def test_wf_cold_start_initialises_epic_from_structured_issue(tmp_path: Path) -> None:
    project = _project(tmp_path)
    bin_dir = tmp_path / "bin"
    _make_gh_stub(
        bin_dir,
        {
            "number": 42,
            "title": "Comment publishing",
            "body": _structured_body(),
            "updated_at": "2026-01-02T12:34:56Z",
        },
    )

    proc = _run(project, "wf", "--epic", "42", env=_stub_env(bin_dir))

    assert proc.returncode == 0, proc.stderr
    assert "initialised E42 from GitHub issue with spark.md and EPIC.md" in proc.stdout
    epic_dir = project / ".woof" / "epics" / "E42"
    assert (
        (epic_dir / "spark.md")
        .read_text()
        .startswith("# Comment publishing\n\nPreserve this teammate-written intent.")
    )
    epic_text = (epic_dir / "EPIC.md").read_text()
    front = yaml.safe_load(epic_text[4 : epic_text.find("\n---\n", 4)])
    assert front["epic_id"] == 42
    assert front["title"] == "Comment publishing"
    assert front["observable_outcomes"][1]["deprecated"] is True
    assert front["observable_outcomes"][1]["replaced_by"] == "O3"
    assert front["contract_decisions"][0]["openapi_ref"] == (
        "spec/openapi.yaml#/paths/~1comments/post"
    )
    assert front["contract_decisions"][1]["pydantic_ref"] == (
        "webapp/schemas/comment.py:CommentCreate"
    )
    assert front["acceptance_criteria"] == [
        "All outcomes verified by tests in diff.",
        "Contract decisions validate via native tooling.",
    ]
    last_sync = json.loads((epic_dir / ".last-sync").read_text())
    assert last_sync["issue_number"] == 42
    assert last_sync["updated_at"] == "2026-01-02T12:34:56Z"
    assert len(last_sync["body_sha256"]) == 64
    events = [json.loads(line) for line in (epic_dir / "epic.jsonl").read_text().splitlines()]
    assert [event["event"] for event in events] == ["spark_created", "github_synced"]


def test_wf_cold_start_without_structured_sections_seeds_only_spark(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    bin_dir = tmp_path / "bin"
    _make_gh_stub(
        bin_dir,
        {
            "number": 7,
            "title": "Explore the idea",
            "body": "Loose spark prose only.\n\nNo structured Definition yet.",
            "updated_at": "2026-01-03T00:00:00Z",
        },
    )

    proc = _run(project, "wf", "--epic", "7", "--format", "json", env=_stub_env(bin_dir))

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] == "initialised"
    epic_dir = project / ".woof" / "epics" / "E7"
    assert (epic_dir / "spark.md").is_file()
    assert not (epic_dir / "EPIC.md").exists()


def test_wf_cold_start_fails_loud_when_issue_fetch_fails(tmp_path: Path) -> None:
    project = _project(tmp_path)
    bin_dir = tmp_path / "bin"
    _make_gh_stub(bin_dir, fail=True)

    proc = _run(project, "wf", "--epic", "404", env=_stub_env(bin_dir))

    assert proc.returncode == 2
    assert "E404 not found" in proc.stderr
    assert not (project / ".woof" / "epics" / "E404").exists()

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from woof.lib.audit import prepare_commit_audit
from woof.lib.audit_bundle import NonPortableTranscriptError, bundle_claude_transcripts

REPO_ROOT = Path(__file__).resolve().parents[2]
WOOF_BIN = REPO_ROOT / "bin" / "woof"


def test_prepare_commit_audit_redacts_known_and_custom_secrets(tmp_path: Path) -> None:
    woof_dir = tmp_path / ".woof"
    woof_dir.mkdir()
    (woof_dir / "agents.toml").write_text(
        """\
[audit]
max_bytes = 4096
redact_patterns = ["PROJECT_SECRET_[A-Z]+"]
"""
    )
    (tmp_path / "env.local.sh").write_text(
        "export INTERNAL_API_TOKEN='env-token-value'\nPUBLIC_VALUE=left-visible\n"
    )
    (tmp_path / ".gts-auth.json").write_text('{"access_token": "gts-token-value"}\n')

    epic_dir = woof_dir / "epics" / "E1"
    audit_dir = epic_dir / "audit"
    audit_dir.mkdir(parents=True)
    audit_file = audit_dir / "cod-critiquer.output"
    audit_file.write_text(
        "Bearer live-oauth-token\n"
        "aws=AKIA1234567890ABCDEF\n"
        "jwt=eyJabc.def456.ghi789\n"
        "token=inline-secret\n"
        "env-token-value\n"
        "gts-token-value\n"
        "PROJECT_SECRET_ALPHA\n"
    )

    summaries = prepare_commit_audit(tmp_path, epic_dir)

    text = audit_file.read_text()
    assert "live-oauth-token" not in text
    assert "AKIA1234567890ABCDEF" not in text
    assert "eyJabc.def456.ghi789" not in text
    assert "inline-secret" not in text
    assert "env-token-value" not in text
    assert "gts-token-value" not in text
    assert "PROJECT_SECRET_ALPHA" not in text
    assert "[REDACTED:bearer_token]" in text
    assert "[REDACTED:aws_access_key]" in text
    assert "[REDACTED:jwt]" in text
    assert "[REDACTED:secret_assignment]" in text
    assert "[REDACTED:env_local]" in text
    assert "[REDACTED:gts_auth]" in text
    assert "[REDACTED:custom_pattern]" in text
    assert summaries[0].redacted is True
    assert summaries[0].truncated is False


def test_prepare_commit_audit_caps_large_files_and_preserves_raw(tmp_path: Path) -> None:
    woof_dir = tmp_path / ".woof"
    woof_dir.mkdir()
    (woof_dir / "agents.toml").write_text(
        """\
[audit]
max_bytes = 180
"""
    )
    epic_dir = woof_dir / "epics" / "E2"
    audit_dir = epic_dir / "audit"
    audit_dir.mkdir(parents=True)
    audit_file = audit_dir / "cod-critiquer.output"
    original = "line 1\n" + ("x" * 500) + "\n"
    audit_file.write_text(original)

    summaries = prepare_commit_audit(tmp_path, epic_dir)

    text = audit_file.read_text()
    assert len(text.encode()) <= 180
    assert "... [truncated, full output at .woof/epics/E2/audit/raw/" in text
    assert summaries[0].truncated is True
    assert summaries[0].raw_path == ".woof/epics/E2/audit/raw/cod-critiquer.output"
    raw_path = tmp_path / summaries[0].raw_path
    assert raw_path.read_text() == original


def test_prepare_commit_audit_honours_disabled_policy(tmp_path: Path) -> None:
    woof_dir = tmp_path / ".woof"
    woof_dir.mkdir()
    (woof_dir / "agents.toml").write_text(
        """\
[audit]
enabled = false
max_bytes = 10
"""
    )
    epic_dir = woof_dir / "epics" / "E3"
    audit_dir = epic_dir / "audit"
    audit_dir.mkdir(parents=True)
    audit_file = audit_dir / "cod-critiquer.output"
    original = "Bearer live-oauth-token\n" + ("x" * 200)
    audit_file.write_text(original)

    summaries = prepare_commit_audit(tmp_path, epic_dir)

    assert summaries == []
    assert audit_file.read_text() == original


def test_bundle_claude_transcripts_copies_portable_references(tmp_path: Path) -> None:
    repo = tmp_path / "project"
    epic_dir = repo / ".woof" / "epics" / "E7"
    epic_dir.mkdir(parents=True)
    project_slug = "-tmp-project"
    session_id = "00000000-0000-0000-0000-000000000001"
    reference = f"~/.claude/projects/{project_slug}/{session_id}.jsonl"
    (epic_dir / "dispatch.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"event": "subprocess_spawned", "at": "2026-05-19T00:00:00Z"}),
                json.dumps(
                    {
                        "event": "subprocess_returned",
                        "at": "2026-05-19T00:00:01Z",
                        "claude_transcript_path": reference,
                    }
                ),
                json.dumps(
                    {
                        "event": "subprocess_returned",
                        "at": "2026-05-19T00:00:02Z",
                        "claude_transcript_path": reference,
                    }
                ),
            ]
        )
        + "\n"
    )
    home = tmp_path / "home"
    source = home / ".claude" / "projects" / project_slug / f"{session_id}.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text('{"message":"kept"}\n')

    result = bundle_claude_transcripts(repo, "7", home=home)

    destination = epic_dir / "audit" / "claude-code" / project_slug / f"{session_id}.jsonl"
    assert result.ok is True
    assert [item.reference for item in result.copied] == [reference]
    assert result.missing == ()
    assert destination.read_text() == '{"message":"kept"}\n'


def test_bundle_claude_transcripts_reports_missing_sources(tmp_path: Path) -> None:
    repo = tmp_path / "project"
    epic_dir = repo / ".woof" / "epics" / "E8"
    epic_dir.mkdir(parents=True)
    reference = "~/.claude/projects/-tmp-project/missing.jsonl"
    (epic_dir / "dispatch.jsonl").write_text(
        json.dumps(
            {
                "event": "subprocess_returned",
                "at": "2026-05-19T00:00:01Z",
                "claude_transcript_path": reference,
            }
        )
        + "\n"
    )

    result = bundle_claude_transcripts(repo, "E8", home=tmp_path / "home")

    assert result.ok is False
    assert result.copied == ()
    assert [item.reference for item in result.missing] == [reference]


def test_bundle_claude_transcripts_rejects_non_portable_paths(tmp_path: Path) -> None:
    repo = tmp_path / "project"
    epic_dir = repo / ".woof" / "epics" / "E9"
    epic_dir.mkdir(parents=True)
    (epic_dir / "dispatch.jsonl").write_text(
        json.dumps(
            {
                "event": "subprocess_returned",
                "at": "2026-05-19T00:00:01Z",
                "claude_transcript_path": "/home/ryan/.claude/projects/proj/session.jsonl",
            }
        )
        + "\n"
    )

    try:
        bundle_claude_transcripts(repo, "E9", home=tmp_path / "home")
    except NonPortableTranscriptError as exc:
        assert "not portable" in str(exc)
    else:
        raise AssertionError("expected NonPortableTranscriptError")


def test_audit_bundle_cli_copies_transcripts_without_absolute_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "project"
    epic_dir = repo / ".woof" / "epics" / "E10"
    epic_dir.mkdir(parents=True)
    project_slug = "-tmp-project"
    session_id = "00000000-0000-0000-0000-000000000010"
    reference = f"~/.claude/projects/{project_slug}/{session_id}.jsonl"
    (epic_dir / "dispatch.jsonl").write_text(
        json.dumps(
            {
                "event": "subprocess_returned",
                "at": "2026-05-19T00:00:01Z",
                "claude_transcript_path": reference,
            }
        )
        + "\n"
    )
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    source = home / ".claude" / "projects" / project_slug / f"{session_id}.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text('{"message":"cli"}\n')

    proc = subprocess.run(
        [str(WOOF_BIN), "audit-bundle", "E10"],
        capture_output=True,
        text=True,
        cwd=repo,
    )

    assert proc.returncode == 0, proc.stderr
    assert "/home/" not in proc.stdout
    assert str(tmp_path) not in proc.stdout
    assert reference in proc.stdout
    assert (
        epic_dir / "audit" / "claude-code" / project_slug / f"{session_id}.jsonl"
    ).read_text() == '{"message":"cli"}\n'

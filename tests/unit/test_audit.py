from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.support import DEFAULT_PROJECT_KEY, seed_project_config
from woof import state
from woof.lib.audit import redact_audit_artefacts, scan_text_for_secrets
from woof.lib.audit_bundle import NonPortableTranscriptError, bundle_claude_transcripts

REPO_ROOT = Path(__file__).resolve().parents[2]
WOOF_BIN = REPO_ROOT / "bin" / "woof"
KEY = DEFAULT_PROJECT_KEY


def _audit_file(epic_id: int, name: str = "cod-critiquer.output") -> Path:
    audit_dir = state.audit_dir(KEY, epic_id)
    audit_dir.mkdir(parents=True, exist_ok=True)
    return audit_dir / name


def test_redact_audit_artefacts_redacts_known_and_custom_secrets(tmp_path: Path) -> None:
    seed_project_config(
        {
            "dispatch": {
                "audit": {
                    "max_bytes": 4096,
                    "redact_patterns": ["PROJECT_SECRET_[A-Z]+"],
                }
            }
        }
    )
    (tmp_path / "env.local.sh").write_text(
        "export INTERNAL_API_TOKEN='env-token-value'\nPUBLIC_VALUE=left-visible\n"
    )
    (tmp_path / ".gts-auth.json").write_text('{"access_token": "gts-token-value"}\n')

    audit_file = _audit_file(1)
    audit_file.write_text(
        "Bearer live-oauth-token\n"
        "aws=AKIA1234567890ABCDEF\n"
        "jwt=eyJabc.def456.ghi789\n"
        "token=inline-secret\n"
        "env-token-value\n"
        "gts-token-value\n"
        "PROJECT_SECRET_ALPHA\n"
    )

    summaries = redact_audit_artefacts(KEY, 1, repo_root=tmp_path)

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


def test_redact_audit_artefacts_caps_large_files_and_preserves_raw(tmp_path: Path) -> None:
    seed_project_config({"dispatch": {"audit": {"max_bytes": 180}}})
    audit_file = _audit_file(2)
    original = "line 1\n" + ("x" * 500) + "\n"
    audit_file.write_text(original)

    summaries = redact_audit_artefacts(KEY, 2, repo_root=tmp_path)

    text = audit_file.read_text()
    assert len(text.encode()) <= 180
    assert "... [truncated, full output at raw/" in text
    assert summaries[0].truncated is True
    # Summary paths are relative to the epic's audit directory, never to the repo.
    assert summaries[0].raw_path == "raw/cod-critiquer.output"
    raw_path = state.audit_dir(KEY, 2) / summaries[0].raw_path
    assert raw_path.read_text() == original


def test_redact_audit_artefacts_honours_disabled_policy(tmp_path: Path) -> None:
    seed_project_config({"dispatch": {"audit": {"enabled": False, "max_bytes": 10}}})
    audit_file = _audit_file(3)
    original = "Bearer live-oauth-token\n" + ("x" * 200)
    audit_file.write_text(original)

    summaries = redact_audit_artefacts(KEY, 3, repo_root=tmp_path)

    assert summaries == []
    assert audit_file.read_text() == original


def test_bundle_claude_transcripts_copies_portable_references(tmp_path: Path) -> None:
    epic_dir = state.epic_dir(KEY, 7)
    epic_dir.mkdir(parents=True)
    project_slug = "-tmp-project"
    session_id = "00000000-0000-0000-0000-000000000001"
    reference = f"~/.claude/projects/{project_slug}/{session_id}.jsonl"
    state.dispatch_events_path(KEY, 7).write_text(
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

    result = bundle_claude_transcripts(KEY, "7", home=home)

    destination = state.audit_dir(KEY, 7) / "claude-code" / project_slug / f"{session_id}.jsonl"
    assert result.ok is True
    assert [item.reference for item in result.copied] == [reference]
    assert result.missing == ()
    assert destination.read_text() == '{"message":"kept"}\n'


def test_bundle_claude_transcripts_reports_missing_sources(tmp_path: Path) -> None:
    state.epic_dir(KEY, 8).mkdir(parents=True)
    reference = "~/.claude/projects/-tmp-project/missing.jsonl"
    state.dispatch_events_path(KEY, 8).write_text(
        json.dumps(
            {
                "event": "subprocess_returned",
                "at": "2026-05-19T00:00:01Z",
                "claude_transcript_path": reference,
            }
        )
        + "\n"
    )

    result = bundle_claude_transcripts(KEY, "E8", home=tmp_path / "home")

    assert result.ok is False
    assert result.copied == ()
    assert [item.reference for item in result.missing] == [reference]


def test_bundle_claude_transcripts_rejects_non_portable_paths(tmp_path: Path) -> None:
    state.epic_dir(KEY, 9).mkdir(parents=True)
    state.dispatch_events_path(KEY, 9).write_text(
        json.dumps(
            {
                "event": "subprocess_returned",
                "at": "2026-05-19T00:00:01Z",
                "claude_transcript_path": "/home/ryan/.claude/projects/proj/session.jsonl",
            }
        )
        + "\n"
    )

    with pytest.raises(NonPortableTranscriptError, match="not portable"):
        bundle_claude_transcripts(KEY, "E9", home=tmp_path / "home")


def test_audit_bundle_cli_copies_transcripts_from_portable_references(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "project"
    repo.mkdir()
    state.epic_dir(KEY, 10).mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    project_slug = "-tmp-project"
    session_id = "00000000-0000-0000-0000-000000000010"
    reference = f"~/.claude/projects/{project_slug}/{session_id}.jsonl"
    state.dispatch_events_path(KEY, 10).write_text(
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
    assert reference in proc.stdout
    # The transcript lands in the operator home, never in the driven repo.
    assert not (repo / ".woof").exists()
    assert (
        state.audit_dir(KEY, 10) / "claude-code" / project_slug / f"{session_id}.jsonl"
    ).read_text() == '{"message":"cli"}\n'


def test_scan_text_for_secrets_flags_high_signal_tokens() -> None:
    text = (
        "intro line\n"
        "leaked = sk-abcdefghijklmnopqrstuvwxyz0123\n"
        "aws = AKIA1234567890ABCDEF\n"
        "nothing here\n"
    )

    hits = scan_text_for_secrets(text)

    assert {hit.reason for hit in hits} == {"openai_key", "aws_access_key"}
    assert {hit.line for hit in hits} == {2, 3}


def test_scan_text_for_secrets_ignores_prose_and_assignments() -> None:
    text = (
        "The password field is validated before save.\n"
        "Pass a Bearer token in the Authorization header.\n"
        "api_key: see the deployment runbook for where it is configured\n"
    )

    assert scan_text_for_secrets(text) == []

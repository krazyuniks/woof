from __future__ import annotations

from pathlib import Path

from woof.lib.audit import prepare_commit_audit


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

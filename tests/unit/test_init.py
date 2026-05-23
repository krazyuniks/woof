"""Black-box tests for ``woof init``."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _env() -> dict[str, str]:
    uv = shutil.which("uv")
    sh = shutil.which("sh")
    ajv = shutil.which("ajv")
    assert uv is not None
    assert sh is not None
    env = os.environ.copy()
    bin_dirs = [Path(uv).parent, Path(sh).parent]
    if ajv is not None:
        bin_dirs.append(Path(ajv).parent)
    env["PATH"] = os.pathsep.join(str(p) for p in bin_dirs)
    return env


def test_init_creates_starter_config_and_gitignore_block(tmp_path: Path, run_woof) -> None:
    proc = run_woof("init", "--project-root", str(tmp_path), env=_env())

    assert proc.returncode == 0, proc.stderr + proc.stdout
    woof_dir = tmp_path / ".woof"
    assert (woof_dir / "prerequisites.toml").is_file()
    assert (woof_dir / "agents.toml").is_file()
    assert (woof_dir / "quality-gates.toml").is_file()
    assert (woof_dir / "test-markers.toml").is_file()
    assert not (woof_dir / "docs-paths.toml").exists()

    prereq = (woof_dir / "prerequisites.toml").read_text()
    assert "<replace>/<replace>" in prereq
    agents = (woof_dir / "agents.toml").read_text()
    assert "Runtime model: trusted-local automation" in agents

    gitignore = (tmp_path / ".gitignore").read_text()
    assert "# >>> woof" in gitignore
    assert ".woof/.current-epic" in gitignore
    assert ".woof/.preflight-floor" in gitignore
    assert ".woof/codebase/tags" in gitignore
    assert "# <<< woof" in gitignore


def test_init_is_idempotent(tmp_path: Path, run_woof) -> None:
    first = run_woof("init", "--project-root", str(tmp_path), env=_env())
    assert first.returncode == 0, first.stderr + first.stdout

    prereq_path = tmp_path / ".woof" / "prerequisites.toml"
    prereq_path.write_text('# user-edited\n[tracker]\nkind = "github"\nrepo = "example/project"\n')
    gitignore_before = (tmp_path / ".gitignore").read_text()

    second = run_woof("init", "--project-root", str(tmp_path), env=_env())
    assert second.returncode == 0, second.stderr + second.stdout
    assert prereq_path.read_text().startswith("# user-edited"), "user edits must be preserved"
    assert (tmp_path / ".gitignore").read_text() == gitignore_before, (
        "gitignore block must not duplicate"
    )
    assert "skipped" in second.stdout


def test_init_force_overwrites_existing_files(tmp_path: Path, run_woof) -> None:
    first = run_woof("init", "--project-root", str(tmp_path), env=_env())
    assert first.returncode == 0, first.stderr + first.stdout
    prereq_path = tmp_path / ".woof" / "prerequisites.toml"
    prereq_path.write_text("# user-edited\n")

    forced = run_woof("init", "--project-root", str(tmp_path), "--force", env=_env())
    assert forced.returncode == 0, forced.stderr + forced.stdout
    assert "<replace>/<replace>" in prereq_path.read_text()
    assert "updated" in forced.stdout


def test_init_with_docs_paths_scaffolds_optional_file(tmp_path: Path, run_woof) -> None:
    proc = run_woof(
        "init",
        "--project-root",
        str(tmp_path),
        "--with-docs-paths",
        env=_env(),
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    docs_paths = tmp_path / ".woof" / "docs-paths.toml"
    assert docs_paths.is_file()
    assert "code_pattern" in docs_paths.read_text()


def test_init_default_tracker_is_github(tmp_path: Path, run_woof) -> None:
    proc = run_woof("init", "--project-root", str(tmp_path), env=_env())
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "tracker: github" in proc.stdout

    prereq = (tmp_path / ".woof" / "prerequisites.toml").read_text()
    assert 'kind = "github"' in prereq
    assert 'repo = "<replace>/<replace>"' in prereq
    assert 'gh = "2.0+"' in prereq


def test_init_tracker_local_scaffolds_local_tracker(tmp_path: Path, run_woof) -> None:
    proc = run_woof("init", "--project-root", str(tmp_path), "--tracker", "local", env=_env())
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "tracker: local" in proc.stdout

    prereq = (tmp_path / ".woof" / "prerequisites.toml").read_text()
    assert 'kind = "local"' in prereq
    assert "repo =" not in prereq, "local tracker must not scaffold a repo line"
    assert "gh = " not in prereq, "local tracker must not require the gh CLI"

    # The local tracker block carries no placeholder, so the scaffold validates
    # as-is — unlike the github tracker, whose `repo` needs a real value first.
    validate = run_woof(
        "validate",
        "--schema",
        "prerequisites",
        str(tmp_path / ".woof" / "prerequisites.toml"),
        env=_env(),
    )
    assert validate.returncode == 0, (
        f"local prerequisites.toml did not validate: {validate.stderr + validate.stdout}"
    )


def test_init_preserves_existing_gitignore_content(tmp_path: Path, run_woof) -> None:
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("node_modules/\n.env\n")

    proc = run_woof("init", "--project-root", str(tmp_path), env=_env())
    assert proc.returncode == 0, proc.stderr + proc.stdout
    body = gitignore.read_text()
    assert body.startswith("node_modules/\n.env\n")
    assert "# >>> woof" in body
    assert ".woof/.current-epic" in body


def test_init_updates_existing_managed_block(tmp_path: Path, run_woof) -> None:
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("node_modules/\n# >>> woof\n.woof/old-entry\n# <<< woof\n")

    proc = run_woof("init", "--project-root", str(tmp_path), env=_env())
    assert proc.returncode == 0, proc.stderr + proc.stdout
    body = gitignore.read_text()
    assert "node_modules/\n" in body
    assert ".woof/old-entry" not in body
    assert ".woof/.current-epic" in body
    assert body.count("# >>> woof") == 1


def test_init_templates_validate_against_schemas(tmp_path: Path, run_woof) -> None:
    proc = run_woof("init", "--project-root", str(tmp_path), env=_env())
    assert proc.returncode == 0, proc.stderr + proc.stdout
    woof_dir = tmp_path / ".woof"

    for filename, schema in (
        ("agents.toml", "agents"),
        ("test-markers.toml", "test-markers"),
    ):
        validate = run_woof(
            "validate",
            "--schema",
            schema,
            str(woof_dir / filename),
            env=_env(),
        )
        assert validate.returncode == 0, (
            f"{filename} did not validate against {schema}: {validate.stderr + validate.stdout}"
        )

    forced = run_woof(
        "init", "--project-root", str(tmp_path), "--with-docs-paths", "--force", env=_env()
    )
    assert forced.returncode == 0, forced.stderr + forced.stdout
    docs_validate = run_woof(
        "validate",
        "--schema",
        "docs-paths",
        str(woof_dir / "docs-paths.toml"),
        env=_env(),
    )
    assert docs_validate.returncode == 0, (
        f"docs-paths.toml did not validate: {docs_validate.stderr + docs_validate.stdout}"
    )


def test_init_help_lists_command(run_woof) -> None:
    proc = run_woof("--help", env=_env())
    assert proc.returncode == 0
    assert "init" in proc.stdout


def test_init_outputs_next_steps(tmp_path: Path, run_woof) -> None:
    proc = run_woof("init", "--project-root", str(tmp_path), env=_env())
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "Next steps" in proc.stdout
    assert "claude /login" in proc.stdout
    assert "codex login" in proc.stdout
    assert "woof preflight" in proc.stdout
    assert "woof wf new" in proc.stdout, "next steps must reach the first epic"
    assert "docs/consumers.md" in proc.stdout, "next steps must point at the walkthrough"


def test_init_json_validate_quality_gates_placeholder_is_documented(
    tmp_path: Path, run_woof
) -> None:
    """quality-gates.toml ships with a <replace> placeholder; validating it should fail loud."""

    proc = run_woof("init", "--project-root", str(tmp_path), env=_env())
    assert proc.returncode == 0, proc.stderr + proc.stdout
    validate = run_woof(
        "validate",
        "--schema",
        "quality-gates",
        str(tmp_path / ".woof" / "quality-gates.toml"),
        env=_env(),
    )
    assert validate.returncode == 0, validate.stdout
    text = (tmp_path / ".woof" / "quality-gates.toml").read_text()
    assert "<replace" in text


def test_init_handles_missing_project_root_argument(tmp_path: Path, monkeypatch, run_woof) -> None:
    monkeypatch.chdir(tmp_path)
    proc = run_woof("init", env=_env())
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert (tmp_path / ".woof" / "prerequisites.toml").is_file()

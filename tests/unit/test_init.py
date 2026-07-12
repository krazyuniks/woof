"""Black-box tests for ``woof init`` (ADR-017).

Init writes exactly one file, and it writes it into the operator home:
``~/.woof/config/projects/<key>.toml``. The driven repository is left alone.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from woof.cli.init import (
    GITHUB_REPO_PLACEHOLDER,
    InitError,
    _parse_github_repo,
    run_init,
)
from woof.paths import project_config_path

REPO_ROOT = Path(__file__).resolve().parents[2]

PROJECT_KEY = "demo-project"

_HAS_GIT = shutil.which("git") is not None


def _git_init_repo(path: Path, *, origin: str | None = None) -> None:
    """Initialise a git repo at ``path``, optionally with an ``origin`` remote."""
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    if origin is not None:
        subprocess.run(
            ["git", "remote", "add", "origin", origin],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        )


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


def _config_text(key: str = PROJECT_KEY) -> str:
    return project_config_path(key).read_text()


def _init(*args: str, run_woof, project: str = PROJECT_KEY, project_root: Path | None = None):
    argv = ["init", "--project", project]
    if project_root is not None:
        argv += ["--project-root", str(project_root)]
    return run_woof(*argv, *args, env=_env())


def test_init_writes_one_config_into_the_operator_home(tmp_path: Path, run_woof) -> None:
    proc = _init(run_woof=run_woof, project_root=tmp_path)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    config_path = project_config_path(PROJECT_KEY)
    assert config_path.is_file()
    assert str(config_path) in proc.stdout

    body = config_path.read_text()
    assert 'type = "woof_project"' in body
    assert 'profile = "B"' in body
    assert "default_run_profile" in body
    assert 'model = "gpt-5.6-sol"' in body
    assert 'effort = "high"' in body
    assert "[tracker]" in body
    assert "Runtime model: trusted-local automation" in body


def test_init_writes_nothing_into_the_repo(tmp_path: Path, run_woof) -> None:
    """The driven repo carries no trace of the engine: no .woof/, no .gitignore edit."""

    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("node_modules/\n.env\n")

    proc = _init(run_woof=run_woof, project_root=tmp_path)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert not (tmp_path / ".woof").exists()
    assert gitignore.read_text() == "node_modules/\n.env\n"
    assert [child.name for child in tmp_path.iterdir()] == [".gitignore"]


def test_init_refuses_to_overwrite_an_existing_config(tmp_path: Path, run_woof) -> None:
    first = _init(run_woof=run_woof, project_root=tmp_path)
    assert first.returncode == 0, first.stderr + first.stdout
    config_path = project_config_path(PROJECT_KEY)
    config_path.write_text("# user-edited\n")

    second = _init(run_woof=run_woof, project_root=tmp_path)

    assert second.returncode == 2
    assert "already exists" in second.stderr
    assert "--force" in second.stderr
    assert config_path.read_text() == "# user-edited\n", "user edits must be preserved"


def test_init_force_overwrites_the_existing_config(tmp_path: Path, run_woof) -> None:
    first = _init(run_woof=run_woof, project_root=tmp_path)
    assert first.returncode == 0, first.stderr + first.stdout
    config_path = project_config_path(PROJECT_KEY)
    config_path.write_text("# user-edited\n")

    # Explicit --tracker github exercises the placeholder path (the test PATH has
    # no git, so no remote is reachable and the slug stays a placeholder).
    forced = _init("--tracker", "github", "--force", run_woof=run_woof, project_root=tmp_path)

    assert forced.returncode == 0, forced.stderr + forced.stdout
    assert GITHUB_REPO_PLACEHOLDER in config_path.read_text()
    assert "updated" in forced.stdout


def test_init_refuses_to_overwrite_from_the_python_api(tmp_path: Path) -> None:
    run_init(tmp_path, project_key=PROJECT_KEY)

    with pytest.raises(InitError) as excinfo:
        run_init(tmp_path, project_key=PROJECT_KEY)

    assert "already exists" in str(excinfo.value)
    assert "--force" in str(excinfo.value)


def test_init_with_docs_paths_scaffolds_the_optional_section(tmp_path: Path, run_woof) -> None:
    proc = _init("--with-docs-paths", run_woof=run_woof, project_root=tmp_path)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    body = _config_text()
    assert "[[docs_paths.mappings]]" in body
    assert "code_pattern" in body


def test_init_omits_docs_paths_by_default(tmp_path: Path, run_woof) -> None:
    proc = _init(run_woof=run_woof, project_root=tmp_path)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    # The template documents the section in a comment; it must not declare one.
    assert "[[docs_paths.mappings]]" not in _config_text()


def test_init_default_infers_local_without_github_remote(tmp_path: Path, run_woof) -> None:
    # No --tracker and no reachable github remote (git is off the test PATH) -> local.
    proc = _init(run_woof=run_woof, project_root=tmp_path)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "tracker: local" in proc.stdout

    body = _config_text()
    assert 'kind = "local"' in body
    assert "repo =" not in body
    assert "gh = " not in body


def test_init_tracker_local_scaffolds_local_tracker(tmp_path: Path, run_woof) -> None:
    proc = _init("--tracker", "local", run_woof=run_woof, project_root=tmp_path)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "tracker: local" in proc.stdout

    body = _config_text()
    assert 'kind = "local"' in body
    assert "repo =" not in body, "local tracker must not scaffold a repo line"
    assert "gh = " not in body, "local tracker must not require the gh CLI"


def test_init_config_validates_against_the_project_config_schema(tmp_path: Path, run_woof) -> None:
    """The scaffolded config detects as project-config from its path and validates."""

    proc = _init(
        "--tracker", "local", "--with-docs-paths", run_woof=run_woof, project_root=tmp_path
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout

    validate = run_woof("validate", str(project_config_path(PROJECT_KEY)), env=_env())

    assert validate.returncode == 0, validate.stderr + validate.stdout
    assert "valid (project-config)" in validate.stdout


def test_init_scaffolds_replace_placeholders(tmp_path: Path, run_woof) -> None:
    """The scaffold ships explicit placeholders so preflight refuses unedited boilerplate."""

    proc = _init("--tracker", "github", run_woof=run_woof, project_root=tmp_path)
    assert proc.returncode == 0, proc.stderr + proc.stdout

    body = _config_text()
    assert "<replace project verification command" in body
    assert "<replace project test command" in body
    assert f'repo = "{GITHUB_REPO_PLACEHOLDER}"' in body


def test_init_help_lists_command(run_woof) -> None:
    proc = run_woof("--help", env=_env())
    assert proc.returncode == 0
    assert "init" in proc.stdout


def test_init_outputs_next_steps(tmp_path: Path, run_woof) -> None:
    proc = _init(run_woof=run_woof, project_root=tmp_path)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "Next steps" in proc.stdout
    assert "claude /login" in proc.stdout
    assert "codex login" in proc.stdout
    assert f"woof preflight --project {PROJECT_KEY}" in proc.stdout
    assert "woof wf new" in proc.stdout, "next steps must reach the first epic"
    assert "Run the graph with the command printed by `woof wf new`" in proc.stdout
    assert "skills/woof/references/setup.md" in proc.stdout, (
        "next steps must point at the walkthrough"
    )


def test_init_handles_missing_project_root_argument(tmp_path: Path, monkeypatch, run_woof) -> None:
    monkeypatch.chdir(tmp_path)
    proc = _init(run_woof=run_woof)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert project_config_path(PROJECT_KEY).is_file()
    assert not (tmp_path / ".woof").exists()


def test_init_rejects_an_invalid_project_key(tmp_path: Path, run_woof) -> None:
    proc = _init(run_woof=run_woof, project="Not/A Key", project_root=tmp_path)

    assert proc.returncode == 2
    assert "invalid project key" in proc.stderr


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("git@github.com:acme/widgets.git", "acme/widgets"),
        ("git@github.com:acme/widgets", "acme/widgets"),
        ("https://github.com/acme/widgets.git", "acme/widgets"),
        ("https://github.com/acme/widgets", "acme/widgets"),
        ("https://github.com/acme/widgets/", "acme/widgets"),
        ("https://user@github.com/acme/widgets.git", "acme/widgets"),
        ("ssh://git@github.com/acme/widgets.git", "acme/widgets"),
        ("git://github.com/acme/widgets.git", "acme/widgets"),
        # Non-github hosts and junk fall through to the placeholder.
        ("git@gitlab.com:acme/widgets.git", None),
        ("https://example.com/acme/widgets.git", None),
        ("not a url", None),
        ("", None),
    ],
)
def test_parse_github_repo(url: str, expected: str | None) -> None:
    assert _parse_github_repo(url) == expected


@pytest.mark.skipif(not _HAS_GIT, reason="git not available")
def test_init_default_infers_github_and_slug_from_origin(tmp_path: Path) -> None:
    _git_init_repo(tmp_path, origin="git@github.com:acme/widgets.git")

    result = run_init(tmp_path, project_key=PROJECT_KEY)

    body = _config_text()
    assert 'kind = "github"' in body
    assert 'repo = "acme/widgets"' in body
    assert GITHUB_REPO_PLACEHOLDER not in body
    assert result.tracker == "github"
    assert result.tracker_inferred is True
    assert result.inferred_repo == "acme/widgets"
    assert result.config.action == "created"
    assert result.config.relpath == str(project_config_path(PROJECT_KEY))


@pytest.mark.skipif(not _HAS_GIT, reason="git not available")
def test_init_default_infers_local_when_no_remote(tmp_path: Path) -> None:
    _git_init_repo(tmp_path)  # a git repo, but no remote

    result = run_init(tmp_path, project_key=PROJECT_KEY)

    body = _config_text()
    assert 'kind = "local"' in body
    assert "repo =" not in body
    assert result.tracker == "local"
    assert result.tracker_inferred is True
    assert result.inferred_repo is None


def test_init_default_infers_local_outside_git_repo(tmp_path: Path) -> None:
    # tmp_path is not a git repo; inference finds no remote -> local default.
    result = run_init(tmp_path, project_key=PROJECT_KEY)

    body = _config_text()
    assert 'kind = "local"' in body
    assert result.tracker == "local"
    assert result.inferred_repo is None


@pytest.mark.skipif(not _HAS_GIT, reason="git not available")
def test_init_explicit_github_infers_slug_from_origin(tmp_path: Path) -> None:
    _git_init_repo(tmp_path, origin="https://github.com/acme/widgets.git")

    result = run_init(tmp_path, project_key=PROJECT_KEY, tracker="github")

    body = _config_text()
    assert 'repo = "acme/widgets"' in body
    assert GITHUB_REPO_PLACEHOLDER not in body
    assert result.tracker == "github"
    assert result.tracker_inferred is False
    assert result.inferred_repo == "acme/widgets"


@pytest.mark.skipif(not _HAS_GIT, reason="git not available")
def test_init_explicit_github_without_remote_keeps_placeholder(tmp_path: Path) -> None:
    _git_init_repo(tmp_path)  # a git repo, but no remote

    result = run_init(tmp_path, project_key=PROJECT_KEY, tracker="github")

    assert f'repo = "{GITHUB_REPO_PLACEHOLDER}"' in _config_text()
    assert result.tracker == "github"
    assert result.inferred_repo is None


@pytest.mark.skipif(not _HAS_GIT, reason="git not available")
def test_init_explicit_local_ignores_github_remote(tmp_path: Path) -> None:
    """`--tracker local` must not infer or scaffold a repo line even with a github remote."""
    _git_init_repo(tmp_path, origin="git@github.com:acme/widgets.git")

    result = run_init(tmp_path, project_key=PROJECT_KEY, tracker="local")

    body = _config_text()
    assert 'kind = "local"' in body
    assert "repo =" not in body
    assert result.tracker == "local"
    assert result.tracker_inferred is False
    assert result.inferred_repo is None


def test_init_composes_the_refresh_script_for_declared_languages(tmp_path: Path) -> None:
    """Cartography languages land in the config and compose the repo's refresh script."""

    result = run_init(tmp_path, project_key=PROJECT_KEY, tracker="local", languages=["python"])

    assert result.languages == ("python",)
    assert 'languages = ["python"]' in _config_text()
    assert result.script is not None
    assert result.script.action == "created"
    script = tmp_path / "scripts" / "refresh-cartography"
    assert script.is_file()
    assert os.access(script, os.X_OK)


def test_init_skips_the_refresh_script_without_languages(tmp_path: Path) -> None:
    result = run_init(tmp_path, project_key=PROJECT_KEY, tracker="local")

    assert result.languages == ()
    assert result.script is None
    assert result.script_note is not None
    assert "scripts/refresh-cartography" in result.script_note
    assert not (tmp_path / "scripts").exists()


def test_init_rejects_an_unknown_cartography_language(tmp_path: Path) -> None:
    with pytest.raises(InitError) as excinfo:
        run_init(tmp_path, project_key=PROJECT_KEY, tracker="local", languages=["cobol"])

    assert "unknown cartography language" in str(excinfo.value)
    assert not project_config_path(PROJECT_KEY).exists(), (
        "a rejected language must not leave a config behind"
    )

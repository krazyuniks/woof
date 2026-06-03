"""Black-box tests for ``woof init`` refresh-cartography composition (E1/S3).

These drive real ``git`` and ``ctags`` (the ctags-dependent assertion is gated
on the optional binary, mirroring how the preflight tests gate tree-sitter).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

LANGUAGES = ("python", "go", "typescript", "rust")
FRAGMENT_MARKERS = {
    "python": "woof_add_ctags_language Python",
    "go": "woof_add_ctags_language Go",
    "typescript": "woof_add_ctags_language TypeScript",
    "rust": "woof_add_ctags_language Rust",
}


def _env() -> dict[str, str]:
    # Full host PATH so the composed script can reach git, date, and (optionally)
    # ctags, and so bin/woof can reach uv and ajv.
    return os.environ.copy()


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    (path / "app.py").write_text("def hello():\n    return 1\n")
    (path / "main.go").write_text("package main\n\nfunc main() {}\n")
    (path / "app.ts").write_text("export const answer: number = 42;\n")
    (path / "lib.rs").write_text("pub fn answer() -> i32 {\n    42\n}\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )


def _script(path: Path) -> Path:
    return path / "scripts" / "refresh-cartography"


def _init(tmp_path: Path, run_woof, *languages: str):
    args = ["init", "--project-root", str(tmp_path), "--tracker", "local"]
    for language in languages:
        args += ["--language", language]
    return run_woof(*args, env=_env())


def test_init_composes_executable_script_with_each_fragment(tmp_path: Path, run_woof) -> None:
    _init_git_repo(tmp_path)

    proc = _init(tmp_path, run_woof, *LANGUAGES)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    script = _script(tmp_path)
    assert script.is_file()
    assert os.access(script, os.X_OK)
    assert (script.stat().st_mode & 0o777) == 0o755
    body = script.read_text()
    assert body.startswith("#!/usr/bin/env sh")
    for language in LANGUAGES:
        assert FRAGMENT_MARKERS[language] in body, language

    prereq = (tmp_path / ".woof" / "prerequisites.toml").read_text()
    assert 'languages = ["python", "go", "typescript", "rust"]' in prereq


def test_init_script_composition_is_idempotent(tmp_path: Path, run_woof) -> None:
    _init_git_repo(tmp_path)
    assert _init(tmp_path, run_woof, "python").returncode == 0
    first = _script(tmp_path).read_text()

    second = _init(tmp_path, run_woof, "python")

    assert second.returncode == 0, second.stderr + second.stdout
    assert _script(tmp_path).read_text() == first, "re-compose must be byte-identical"
    assert "skipped  scripts/refresh-cartography" in second.stdout


def test_init_recomposes_when_language_set_changes(tmp_path: Path, run_woof) -> None:
    _init_git_repo(tmp_path)
    assert _init(tmp_path, run_woof, "python").returncode == 0
    assert FRAGMENT_MARKERS["go"] not in _script(tmp_path).read_text()

    changed = _init(tmp_path, run_woof, "python", "go")

    assert changed.returncode == 0, changed.stderr + changed.stdout
    body = _script(tmp_path).read_text()
    assert FRAGMENT_MARKERS["python"] in body
    assert FRAGMENT_MARKERS["go"] in body
    assert "updated  scripts/refresh-cartography" in changed.stdout


def test_init_composes_from_prerequisites_fallback(tmp_path: Path, run_woof) -> None:
    _init_git_repo(tmp_path)
    # First run records languages into [cartography].languages.
    assert _init(tmp_path, run_woof, "python").returncode == 0

    # Re-run with no --language: composition falls back to the existing file.
    fallback = run_woof("init", "--project-root", str(tmp_path), "--tracker", "local", env=_env())

    assert fallback.returncode == 0, fallback.stderr + fallback.stdout
    assert FRAGMENT_MARKERS["python"] in _script(tmp_path).read_text()
    assert "from existing" in fallback.stdout


def test_init_skips_script_when_no_languages_declared(tmp_path: Path, run_woof) -> None:
    _init_git_repo(tmp_path)

    proc = run_woof("init", "--project-root", str(tmp_path), "--tracker", "local", env=_env())

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert not _script(tmp_path).exists()
    assert "skipped scripts/refresh-cartography" in proc.stdout


def test_init_rejects_unknown_cartography_language(tmp_path: Path, run_woof) -> None:
    _init_git_repo(tmp_path)

    proc = _init(tmp_path, run_woof, "cobol")

    assert proc.returncode == 2
    assert "unknown cartography language" in proc.stderr
    assert not _script(tmp_path).exists()


def test_composed_script_emits_schema_valid_freshness(tmp_path: Path, run_woof) -> None:
    _init_git_repo(tmp_path)
    assert _init(tmp_path, run_woof, "python").returncode == 0

    run = subprocess.run(
        [str(_script(tmp_path))], cwd=tmp_path, env=_env(), capture_output=True, text=True
    )
    assert run.returncode == 0, run.stderr + run.stdout

    codebase = tmp_path / ".woof" / "codebase"
    assert (codebase / "files.txt").read_text().strip() != ""

    freshness = codebase / "freshness.json"
    payload = json.loads(freshness.read_text())
    assert set(payload) == {"ts", "git_ref", "age_s", "generator_version"}
    assert payload["age_s"] == 0
    assert payload["generator_version"] == 1

    validate = run_woof("validate", "--schema", "freshness", str(freshness), env=_env())
    assert validate.returncode == 0, validate.stderr + validate.stdout

    # ctags is optional on the test host; gate the index-content assertion on it.
    if shutil.which("ctags") is not None:
        assert (codebase / "tags").read_text().strip() != ""
    else:
        assert (codebase / "tags").is_file()


def test_language_registries_validate_against_amended_schema(run_woof) -> None:
    for language in LANGUAGES:
        registry = REPO_ROOT / "languages" / f"{language}.toml"
        proc = run_woof("validate", "--schema", "language-registry", str(registry), env=_env())
        assert proc.returncode == 0, f"{language}: {proc.stderr + proc.stdout}"

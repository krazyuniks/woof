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

from woof import state
from woof.paths import project_config_path

REPO_ROOT = Path(__file__).resolve().parents[2]

PROJECT_KEY = "demo-project"

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


def _make_universal_ctags_stub(bin_dir: Path) -> None:
    """Write a stub ctags that satisfies preflight (--version) and refresh-cartography (-f)."""
    stub = bin_dir / "ctags"
    stub.write_text(
        "#!/usr/bin/env sh\n"
        'if [ "$1" = "--version" ]; then\n'
        '  echo "Universal Ctags 6.1.0(+sandbox), Copyright (C) 2015-2023 Universal Ctags Team"\n'
        "  exit 0\n"
        "fi\n"
        "while [ $# -gt 0 ]; do\n"
        '  case "$1" in\n'
        '    -f) printf "stub_sym\\t%s\\t1\\n" "$(pwd)" > "$2"; shift 2;;\n'
        "    *) shift;;\n"
        "  esac\n"
        "done\n"
    )
    stub.chmod(0o755)


def _env_with_universal_ctags(tmp_path: Path) -> dict[str, str]:
    """Return an environment with a stub Universal Ctags prepended to PATH."""
    stub_bin = tmp_path / "_stub_ctags_bin"
    stub_bin.mkdir(exist_ok=True)
    _make_universal_ctags_stub(stub_bin)
    env = os.environ.copy()
    env["PATH"] = str(stub_bin) + os.pathsep + env.get("PATH", "")
    # Drop the ambient key so the run exercises the key `woof init` baked into
    # the composed script, which is what a post-commit hook run actually uses.
    env.pop("WOOF_PROJECT", None)
    return env


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


def _init(tmp_path: Path, run_woof, *languages: str, force: bool = False):
    args = [
        "init",
        "--project",
        PROJECT_KEY,
        "--project-root",
        str(tmp_path),
        "--tracker",
        "local",
    ]
    if force:
        args.append("--force")
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

    config = project_config_path(PROJECT_KEY).read_text()
    assert 'languages = ["python", "go", "typescript", "rust"]' in config


def test_init_script_composition_is_idempotent(tmp_path: Path, run_woof) -> None:
    _init_git_repo(tmp_path)
    assert _init(tmp_path, run_woof, "python").returncode == 0
    first = _script(tmp_path).read_text()

    second = _init(tmp_path, run_woof, "python", force=True)

    assert second.returncode == 0, second.stderr + second.stdout
    assert _script(tmp_path).read_text() == first, "re-compose must be byte-identical"
    assert "skipped  scripts/refresh-cartography" in second.stdout


def test_init_recomposes_when_language_set_changes(tmp_path: Path, run_woof) -> None:
    _init_git_repo(tmp_path)
    assert _init(tmp_path, run_woof, "python").returncode == 0
    assert FRAGMENT_MARKERS["go"] not in _script(tmp_path).read_text()

    changed = _init(tmp_path, run_woof, "python", "go", force=True)

    assert changed.returncode == 0, changed.stderr + changed.stdout
    body = _script(tmp_path).read_text()
    assert FRAGMENT_MARKERS["python"] in body
    assert FRAGMENT_MARKERS["go"] in body
    assert "updated  scripts/refresh-cartography" in changed.stdout


def test_init_skips_script_when_no_languages_declared(tmp_path: Path, run_woof) -> None:
    _init_git_repo(tmp_path)

    proc = _init(tmp_path, run_woof)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert not _script(tmp_path).exists()
    assert "skipped scripts/refresh-cartography" in proc.stdout


def test_init_rejects_unknown_cartography_language(tmp_path: Path, run_woof) -> None:
    _init_git_repo(tmp_path)

    proc = _init(tmp_path, run_woof, "cobol")

    assert proc.returncode == 2
    assert "unknown cartography language" in proc.stderr
    assert not _script(tmp_path).exists()
    assert not project_config_path(PROJECT_KEY).exists()


def test_composed_script_emits_schema_valid_freshness(tmp_path: Path, run_woof) -> None:
    _init_git_repo(tmp_path)
    assert _init(tmp_path, run_woof, "python").returncode == 0

    env = _env_with_universal_ctags(tmp_path)
    run = subprocess.run(
        [str(_script(tmp_path))], cwd=tmp_path, env=env, capture_output=True, text=True
    )

    codebase = state.codebase_dir(PROJECT_KEY)

    assert run.returncode == 0, run.stderr + run.stdout
    assert (codebase / "files.txt").read_text().strip() != ""
    assert (codebase / "tags").read_text().strip() != ""

    freshness = codebase / "freshness.json"
    payload = json.loads(freshness.read_text())
    assert set(payload) == {"ts", "git_ref", "age_s", "generator_version"}
    assert payload["age_s"] == 0
    assert payload["generator_version"] == 2

    validate = run_woof("validate", "--schema", "freshness", str(freshness), env=_env())
    assert validate.returncode == 0, validate.stderr + validate.stdout


def _env_without_ctags(tmp_path: Path) -> dict[str, str]:
    """Return an environment suitable for running refresh-cartography with no ctags.

    Builds a shadow bin dir inside tmp_path that contains the tools the refresh
    script needs (git, sh, date) but NOT ctags. PATH is set to that dir plus
    any system PATH dirs that do not contain ctags, so the script can run but
    ``command -v ctags`` returns non-zero.
    """
    shadow = tmp_path / "_no_ctags_bin"
    shadow.mkdir(exist_ok=True)
    # Tools the refresh script invokes before reaching the ctags guard.
    for tool in ("git", "sh", "mkdir", "date", "printf"):
        tool_path = shutil.which(tool)
        if tool_path and not (shadow / tool).exists():
            (shadow / tool).symlink_to(tool_path)

    env = os.environ.copy()
    path_dirs = env.get("PATH", "").split(os.pathsep)
    no_ctags_dirs = [d for d in path_dirs if not (Path(d) / "ctags").is_file()]
    env["PATH"] = str(shadow) + os.pathsep + os.pathsep.join(no_ctags_dirs)
    env.pop("WOOF_PROJECT", None)
    return env


def test_refresh_exits_nonzero_when_ctags_absent(tmp_path: Path, run_woof) -> None:
    """Refresh fails loud with exit 1 when ctags is absent and languages are declared (ADR-004)."""
    _init_git_repo(tmp_path)
    assert _init(tmp_path, run_woof, "python").returncode == 0

    run = subprocess.run(
        [str(_script(tmp_path))],
        cwd=tmp_path,
        env=_env_without_ctags(tmp_path),
        capture_output=True,
        text=True,
    )

    assert run.returncode != 0, "refresh must exit non-zero when ctags is absent"
    assert "ctags not found" in run.stderr
    assert "universal-ctags" in run.stderr
    # freshness.json must NOT be written; an empty tags was never written either.
    codebase = state.codebase_dir(PROJECT_KEY)
    assert not (codebase / "freshness.json").exists()
    assert not (codebase / "tags").exists()


def test_language_registries_validate_against_amended_schema(run_woof) -> None:
    for language in LANGUAGES:
        registry = REPO_ROOT / "languages" / f"{language}.toml"
        proc = run_woof("validate", "--schema", "language-registry", str(registry), env=_env())
        assert proc.returncode == 0, f"{language}: {proc.stderr + proc.stdout}"

"""Packaging and install-portability smoke tests for RC-6 / GAP-019.

These tests prove that graph-owned subprocesses shell back into Woof through
the active Python module entry point, not via the source-checkout
``bin/woof`` wrapper. The wheel-install case is exercised end-to-end so an
isolated install of the built artefact remains executable.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from woof.graph import nodes
from woof.paths import tool_root

REPO_ROOT = Path(__file__).resolve().parents[2]


pytestmark = pytest.mark.host_only


def test_built_artifacts_contain_only_release_assets(tmp_path: Path) -> None:
    """Wheel ships runtime assets; sdist also keeps the source checkout wrapper."""

    import shutil

    if shutil.which("uv") is None:
        pytest.skip("uv required for package artifact smoke")

    dist_dir = tmp_path / "dist"
    build = subprocess.run(
        ["uv", "build", "-o", str(dist_dir), str(REPO_ROOT)],
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stdout + build.stderr

    wheel = next(dist_dir.glob("woof-*.whl"))
    sdist = next(dist_dir.glob("woof-*.tar.gz"))

    with zipfile.ZipFile(wheel) as archive:
        wheel_names = set(archive.namelist())
    assert "schemas/agents.schema.json" in wheel_names
    assert "playbooks/critique/work-unit.md" in wheel_names
    assert "languages/python.toml" in wheel_names
    assert "bin/woof" not in wheel_names
    assert not any("__pycache__" in name or name.endswith(".pyc") for name in wheel_names)

    with tarfile.open(sdist, "r:gz") as archive:
        sdist_names = set(archive.getnames())

    def has_sdist_path(suffix: str) -> bool:
        return any(name.endswith(f"/{suffix}") for name in sdist_names)

    assert has_sdist_path("schemas/agents.schema.json")
    assert has_sdist_path("playbooks/critique/work-unit.md")
    assert has_sdist_path("languages/python.toml")
    assert has_sdist_path("bin/woof")
    assert not any("__pycache__" in name or name.endswith(".pyc") for name in sdist_names)


def test_graph_subprocess_argv_uses_active_python_module() -> None:
    """RC-6: shelling back into Woof must use ``sys.executable -m woof``."""

    argv = nodes._woof_subprocess_argv()

    assert argv == [sys.executable, "-m", "woof"]


def test_graph_subprocess_env_includes_source_pythonpath() -> None:
    """PYTHONPATH carries a path that lets the child Python import woof."""

    env = nodes._woof_subprocess_env()
    parts = env.get("PYTHONPATH", "").split(os.pathsep)

    root = tool_root()
    expected_candidates = [
        path for path in (root / "src", root) if (path / "woof" / "__init__.py").is_file()
    ]
    assert expected_candidates, "expected to detect a woof package layout via tool_root()"
    assert any(str(candidate) in parts for candidate in expected_candidates)
    assert env.get("WOOF_TOOL_ROOT") == str(root)


def test_python_dash_m_woof_help_uses_active_interpreter() -> None:
    """``python -m woof --help`` returns 0 in the current test interpreter."""

    proc = subprocess.run(
        [sys.executable, "-m", "woof", "--help"],
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "usage:" in (proc.stdout + proc.stderr).lower()


def test_installed_wheel_runs_graph_subprocess_entry(tmp_path: Path) -> None:
    """Build a wheel, install it isolated, and exercise graph-style re-entry.

    The graph shells back into Woof through
    ``[sys.executable, "-m", "woof", "<subcommand>", ...]``. From a wheel
    install this must:

    1. resolve the ``woof`` package via ``__main__.py`` (proving module entry);
    2. surface bundled schemas, playbooks, and language registries through
       ``tool_root()`` so graph nodes can read prompt templates and schema
       paths from the installed artefact.
    """

    import shutil

    if shutil.which("uv") is None:
        pytest.skip("uv required for wheel build/install smoke")

    dist_dir = tmp_path / "dist"
    build = subprocess.run(
        ["uv", "build", "--wheel", "-o", str(dist_dir), str(REPO_ROOT)],
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    wheels = sorted(dist_dir.glob("woof-*.whl"))
    assert wheels, build.stdout + build.stderr
    wheel = wheels[-1]

    isolated_env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path / "home"),
        "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR", str(tmp_path / "uv-cache")),
    }
    Path(isolated_env["HOME"]).mkdir(parents=True, exist_ok=True)

    help_proc = subprocess.run(
        [
            "uv",
            "run",
            "--isolated",
            "--no-project",
            "--with",
            str(wheel),
            "python",
            "-m",
            "woof",
            "--help",
        ],
        cwd=tmp_path,
        env=isolated_env,
        capture_output=True,
        text=True,
    )
    assert help_proc.returncode == 0, help_proc.stdout + help_proc.stderr
    assert "usage:" in (help_proc.stdout + help_proc.stderr).lower()

    console_proc = subprocess.run(
        [
            "uv",
            "run",
            "--isolated",
            "--no-project",
            "--with",
            str(wheel),
            "woof",
            "--help",
        ],
        cwd=tmp_path,
        env=isolated_env,
        capture_output=True,
        text=True,
    )
    assert console_proc.returncode == 0, console_proc.stdout + console_proc.stderr
    assert "usage:" in (console_proc.stdout + console_proc.stderr).lower()

    probe_script = (
        "from woof.paths import schema_dir, tool_root\n"
        "import sys\n"
        "schemas = schema_dir()\n"
        "missing = [\n"
        "    str(p)\n"
        "    for p in (\n"
        "        schemas / 'agents.schema.json',\n"
        "        schemas / 'plan.schema.json',\n"
        "        tool_root() / 'playbooks' / 'critique' / 'work-unit.md',\n"
        "        tool_root() / 'languages' / 'python.toml',\n"
        "    )\n"
        "    if not p.is_file()\n"
        "]\n"
        "if missing:\n"
        "    print('missing: ' + ' '.join(missing))\n"
        "    sys.exit(1)\n"
        "print(str(tool_root()))\n"
    )
    probe_proc = subprocess.run(
        [
            "uv",
            "run",
            "--isolated",
            "--no-project",
            "--with",
            str(wheel),
            "python",
            "-c",
            probe_script,
        ],
        cwd=tmp_path,
        env=isolated_env,
        capture_output=True,
        text=True,
    )
    assert probe_proc.returncode == 0, probe_proc.stdout + probe_proc.stderr

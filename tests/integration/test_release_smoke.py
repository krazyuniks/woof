"""Release smoke test for RC-B4: Phase B release evidence.

This integration test proves Woof is installable and usable by an arbitrary
consumer from a built wheel, with no dependency on the Woof author's local
agent-skill ecosystem, shell wrappers, or host paths. It:

1. builds a wheel from this checkout;
2. installs it into an isolated virtual environment;
3. runs ``woof init --tracker local`` in a throwaway consumer worktree;
4. confirms the scaffolded ``.woof/`` config is shaped for the local tracker
   and validates against the bundled schema;
5. confirms the Stage 1 Discovery producer nodes build fully self-contained
   dispatch prompts from the installed package - the building-block playbooks
   are embedded and no Woof-author-local skill, wrapper, or host path leaks in.

The Stage 1 check is the portability proof for BHID-001: a stranger running
``woof wf`` against their own repo, without the Woof author's
``~/.claude/plugins`` ecosystem, still receives the full Stage 1 technique set
in the dispatched producer prompt.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.host_only

# Tokens that would prove a Stage 1 producer prompt depends on the Woof author's
# local environment rather than the installed package.
FORBIDDEN_PROMPT_TOKENS = [
    "taches-cc-resources",
    "marketplaces",
    "~/.claude/plugins",
    ".dotfiles",
    "agent-sync",
    "/home/ryan",
    "AskUserQuestion",
    "$ARGUMENTS",
]

# Building-block playbook stems each Stage 1 producer prompt must embed so the
# graph dispatches the full technique set without Woof-author-local skills.
RESEARCH_PLAYBOOKS = sorted(
    [
        "competitive",
        "deep-dive",
        "feasibility",
        "history",
        "landscape",
        "open-source",
        "options",
        "technical",
    ]
)
THINKING_PLAYBOOKS = sorted(
    [
        "10-10-10",
        "5-whys",
        "eisenhower-matrix",
        "first-principles",
        "inversion",
        "occams-razor",
        "one-thing",
        "opportunity-cost",
        "pareto",
        "second-order",
        "swot",
        "via-negativa",
    ]
)

# Probe executed inside the isolated wheel install. It renders the Stage 1
# producer prompts from the installed package and reports what they contain.
STAGE1_PROBE = """\
import json
import re
import sys
from pathlib import Path

from woof.graph.nodes import _discovery_bucket_prompt, _discovery_synthesis_prompt
from woof.paths import tool_root

consumer = Path(sys.argv[1])
forbidden = json.loads(sys.argv[2])
epic_id = 1


def forbidden_hits(text):
    return [token for token in forbidden if token in text]


def playbook_stems(text):
    return sorted(re.findall(r"(?m)^## Building-block playbook: (.+)$", text))


result = {"tool_root": str(tool_root()), "buckets": {}}
for bucket in ("research", "thinking", "ideate"):
    prompt = _discovery_bucket_prompt(consumer, epic_id, bucket)
    result["buckets"][bucket] = {
        "length": len(prompt),
        "playbook_stems": playbook_stems(prompt),
        "forbidden_hits": forbidden_hits(prompt),
    }
synthesis = _discovery_synthesis_prompt(consumer, epic_id)
result["synthesis"] = {
    "length": len(synthesis),
    "forbidden_hits": forbidden_hits(synthesis),
}
print(json.dumps(result))
"""


def _clean_env() -> dict[str, str]:
    """Inherit the host environment but drop vars that could let the installed
    wheel import the source checkout instead of its own bundled package."""

    return {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "WOOF_TOOL_ROOT", "VIRTUAL_ENV"}
    }


def test_release_smoke(tmp_path: Path) -> None:
    """An arbitrary consumer can install Woof and run Stage 1 from the wheel."""

    if shutil.which("uv") is None:
        pytest.skip("uv required for the release smoke test")

    env = _clean_env()

    # 1. Build a wheel from this checkout.
    dist_dir = tmp_path / "dist"
    build = subprocess.run(
        ["uv", "build", "--wheel", "-o", str(dist_dir), str(REPO_ROOT)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    wheels = sorted(dist_dir.glob("woof-*.whl"))
    assert wheels, build.stdout + build.stderr
    wheel = wheels[-1]

    # 2. Install the wheel into an isolated virtual environment.
    venv = tmp_path / "venv"
    created = subprocess.run(
        ["uv", "venv", str(venv)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert created.returncode == 0, created.stdout + created.stderr
    python = venv / "bin" / "python"
    installed = subprocess.run(
        ["uv", "pip", "install", "--python", str(python), str(wheel)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr

    def run_woof(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(python), "-m", "woof", *args],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            env=env,
        )

    # 3. Scaffold a throwaway consumer worktree with the local tracker.
    consumer = tmp_path / "consumer-repo"
    consumer.mkdir()
    init = run_woof("init", "--tracker", "local", "--project-root", str(consumer))
    assert init.returncode == 0, init.stdout + init.stderr
    assert "tracker: local" in init.stdout, init.stdout

    # 4. The scaffold is complete and shaped for a no-remote tracker.
    woof_dir = consumer / ".woof"
    for name in (
        "prerequisites.toml",
        "agents.toml",
        "quality-gates.toml",
        "test-markers.toml",
    ):
        assert (woof_dir / name).is_file(), f"woof init did not scaffold {name}"
    gitignore = (consumer / ".gitignore").read_text()
    assert "# >>> woof" in gitignore, "woof init did not patch .gitignore"

    prerequisites = tomllib.loads((woof_dir / "prerequisites.toml").read_text())
    assert prerequisites["tracker"]["kind"] == "local"
    assert "repo" not in prerequisites["tracker"], "local tracker must not need a repo"
    assert "gh" not in prerequisites.get("infra", {}), "local tracker must not require gh"

    # 5. The wheel-bundled schema validates the wheel-scaffolded config.
    if shutil.which("ajv") is not None:
        validate = run_woof(
            "validate",
            "--schema",
            "prerequisites",
            str(woof_dir / "prerequisites.toml"),
        )
        assert validate.returncode == 0, validate.stdout + validate.stderr

    # 6. Stage 1 producer prompts are self-contained from the installed package.
    probe_file = tmp_path / "stage1_probe.py"
    probe_file.write_text(STAGE1_PROBE)
    probe = subprocess.run(
        [str(python), str(probe_file), str(consumer), json.dumps(FORBIDDEN_PROMPT_TOKENS)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env=env,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    report = json.loads(probe.stdout.strip().splitlines()[-1])

    # The playbooks resolve from the installed wheel, not this source checkout.
    resolved_root = Path(report["tool_root"]).resolve()
    assert resolved_root != REPO_ROOT
    assert REPO_ROOT not in resolved_root.parents

    buckets = report["buckets"]
    # The research and thinking nodes embed their full building-block sets so a
    # consumer without Woof-author-local agent skills still gets every angle.
    assert buckets["research"]["playbook_stems"] == RESEARCH_PLAYBOOKS
    assert buckets["thinking"]["playbook_stems"] == THINKING_PLAYBOOKS
    # The ideate node is self-contained and has no building-block set.
    assert buckets["ideate"]["playbook_stems"] == []
    assert buckets["ideate"]["length"] > 0

    # No producer prompt leaks a Woof-author-local skill, wrapper, or host path.
    for bucket, data in buckets.items():
        assert data["forbidden_hits"] == [], f"{bucket} prompt leaked {data['forbidden_hits']}"
    assert report["synthesis"]["forbidden_hits"] == []
    assert report["synthesis"]["length"] > 0

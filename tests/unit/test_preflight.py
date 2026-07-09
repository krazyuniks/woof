"""Black-box tests for ``woof preflight``."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

STANDARD_AGENTS = """\
[timeouts]
default_minutes = 30

[review_valve]
every_n_work_units = 5
end_of_epic = true

[audit]
enabled = true
max_bytes = 262144
redact_patterns = []
"""

AGENTS_WITH_EXECUTION_OVERLAY = """\
[timeouts]
default_minutes = 30

[review_valve]
every_n_work_units = 5
end_of_epic = true

[audit]
enabled = true
max_bytes = 262144
redact_patterns = []
"""

STANDARD_POLICY = """\
schema_version = 1
default_run_profile = "default"

[delivery]
profile = "B"
repo_root = "."
toolchain_root = "."
base_branch = "main"

[profiles.B]
commit = true
push = true

[verification]
command = "just check"
timeout_seconds = 600

[run_profiles.default.producer]
harness = "codex"
model = "gpt-5.5"
effort = "xhigh"

[run_profiles.default.reviewer]
harness = "claude"
model = "claude-opus-4-7"
effort = "max"

[checks]
floor = [
  "quality-gates",
  "outcome-markers",
  "scope",
  "contract-refs",
  "plan-crossrefs",
  "critique-blocker",
  "commit-transaction",
  "docs-drift",
  "review-valve",
]

[cartography]
floor = "structural"

[drain]
merge_after_ready_pr = true
rerun_after_merge = true
mark_unit_done_after_publish = true
commit_backlog_state = true
stop_when_no_eligible_units = true
"""

POLICY_NO_CARTOGRAPHY = STANDARD_POLICY.replace('floor = "structural"', 'floor = "none"')

PROFILE_A_POLICY = STANDARD_POLICY.replace(
    """[delivery]
profile = "B"
repo_root = "."
toolchain_root = "."
base_branch = "main"

[profiles.B]
commit = true
push = true
""",
    """[delivery]
profile = "A"
repo_root = "."
toolchain_root = "."
base_branch = "main"

[profiles.A]
github_repo = "example/project"
ready_label = "ready"
merge_path_groups = []
terminal_deploy_checks = ["Deploy"]
mergeability_settle_timeout = 15
deploy_wait_timeout = 300

[profiles.A.worktree]
root = "worktrees"
""",
)

CARTOGRAPHY_PREREQS = """\
[infra]
just = "any"
git = "any"
gh = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "github"
repo = "example/project"

[cartography]
summary_min_chars = 40
"""

DESIGN_DOC_BODY = (
    "# Target Architecture\n\n"
    "The estate targets event-driven services behind an API gateway, with "
    "deterministic orchestration and on-disk state as the source of truth.\n"
)


def _write_exe(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env sh\n" + body)
    path.chmod(0o755)


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _init_repo(root: Path) -> None:
    _git(root, "init", "-b", "main")
    (root / "README.md").write_text("fixture\n")
    _git(root, "add", "README.md")
    _git(
        root,
        "-c",
        "user.name=Woof Test",
        "-c",
        "user.email=woof@example.test",
        "commit",
        "-m",
        "init",
    )


def _load_policy_toml(text: str) -> dict:
    return tomllib.loads(text)


def _write_cartography(
    root: Path,
    *,
    target: str | None = DESIGN_DOC_BODY,
    principles: str | None = DESIGN_DOC_BODY,
    mechanical: bool = True,
    script: bool = True,
) -> None:
    """Author a cartography artefact set under ``root`` for a declared contract."""

    codebase = root / ".woof" / "codebase"
    codebase.mkdir(parents=True, exist_ok=True)
    if target is not None:
        (codebase / "TARGET-ARCHITECTURE.md").write_text(target)
    if principles is not None:
        (codebase / "PRINCIPLES.md").write_text(principles)
    if mechanical:
        (codebase / "tags").write_text("main\tsrc/main.py\t1\n")
        (codebase / "files.txt").write_text("src/main.py\n")
        # ts is now() so the default stamp is reliably fresh under the
        # ts-authoritative reader (age_s mirrors the generator's frozen 0).
        (codebase / "freshness.json").write_text(
            json.dumps(
                {
                    "ts": datetime.now(UTC).isoformat(),
                    "git_ref": "abc",
                    "age_s": 0,
                    "generator_version": 1,
                }
            )
            + "\n"
        )
    if script:
        scripts = root / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        _write_exe(scripts / "refresh-cartography", "echo refresh\n")


def _write_project(
    root: Path,
    *,
    prerequisites: str,
    policy: str | None = STANDARD_POLICY,
    agents: str | None = STANDARD_AGENTS,
    quality_gates: str | None = None,
) -> None:
    woof_dir = root / ".woof"
    woof_dir.mkdir()
    if policy is not None:
        (woof_dir / "policy.toml").write_text(policy)
    (woof_dir / "prerequisites.toml").write_text(prerequisites)
    if agents is not None:
        (woof_dir / "agents.toml").write_text(agents)
    if quality_gates is not None:
        (woof_dir / "quality-gates.toml").write_text(quality_gates)


def _with_cartography_contract(prerequisites: str) -> str:
    if "[cartography]" in prerequisites:
        return prerequisites
    return prerequisites.rstrip() + "\n\n[cartography]\nsummary_min_chars = 40\n"


def _write_ready_project(
    root: Path,
    *,
    prerequisites: str,
    policy: str | None = STANDARD_POLICY,
    agents: str | None = STANDARD_AGENTS,
    quality_gates: str | None = None,
) -> None:
    _write_project(
        root,
        prerequisites=_with_cartography_contract(prerequisites),
        policy=policy,
        agents=agents,
        quality_gates=quality_gates,
    )
    _write_cartography(root)


def _env_with_path(bin_dir: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    uv = shutil.which("uv")
    sh = shutil.which("sh")
    assert uv is not None
    assert sh is not None
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join(
        [
            str(bin_dir),
            str(Path(uv).parent),
            str(Path(sh).parent),
        ]
    )
    env.setdefault("ANTHROPIC_API_KEY", "stub-anthropic")
    env.setdefault("OPENAI_API_KEY", "stub-openai")
    if extra:
        env.update(extra)
    return env


def _stub_core_tools(bin_dir: Path) -> None:
    _write_exe(
        bin_dir / "ajv",
        """\
if [ "$1" = "validate" ]; then
  exit 0
fi
echo "ajv 8.0.0"
""",
    )
    _write_exe(bin_dir / "just", 'echo "just 1.2.3"\n')
    _write_exe(bin_dir / "git", 'echo "git version 2.44.0"\n')
    _write_exe(
        bin_dir / "gh",
        """\
if [ "$1" = "api" ]; then
  echo '{"ok":true}'
  exit 0
fi
echo "unexpected gh $*" >&2
exit 2
""",
    )
    _write_exe(bin_dir / "claude", 'echo "claude stub"\n')
    _write_exe(bin_dir / "cld", 'echo "cld stub"\n')
    _write_exe(bin_dir / "codex", 'echo "codex stub"\n')


def _write_current_epic_state(root: Path) -> None:
    epic_dir = root / ".woof" / "epics" / "E5"
    audit_dir = epic_dir / "audit"
    audit_dir.mkdir(parents=True)
    (root / ".woof" / ".current-epic").write_text("E5\n")
    (epic_dir / "plan.json").write_text(
        json.dumps(
            {
                "epic_id": 5,
                "goal": "Expose operator state.",
                "work_units": [
                    {
                        "id": "S1",
                        "title": "Report current state",
                        "summary": "Make the current graph state visible.",
                        "paths": ["src/woof/**/*.py"],
                        "satisfies": ["O1"],
                        "implements_contract_decisions": [],
                        "uses_contract_decisions": [],
                        "deps": [],
                        "tests": {"count": 1, "types": ["unit"]},
                        "state": "in_progress",
                    }
                ],
            }
        )
        + "\n"
    )
    (epic_dir / "gate.md").write_text(
        """---
type: work_unit_gate
stage: 6
work_unit_id: S1
triggered_by:
  - check_1_quality_gates
timestamp: '2026-05-23T10:02:00Z'
---

## Context

Quality failed.
"""
    )
    (epic_dir / "epic.jsonl").write_text(
        json.dumps(
            {
                "event": "work_unit_gate_opened",
                "at": "2026-05-23T10:02:00Z",
                "epic_id": 5,
                "work_unit_id": "S1",
                "gate_type": "work_unit_gate",
                "triggered_by": ["check_1_quality_gates"],
            }
        )
        + "\n"
    )
    (epic_dir / "dispatch.jsonl").write_text(
        json.dumps(
            {
                "event": "subprocess_returned",
                "at": "2026-05-23T10:01:00Z",
                "epic_id": 5,
                "work_unit_id": "S1",
                "role": "primary",
                "adapter": "codex",
                "model": "gpt-5.5",
                "effort": "xhigh",
                "exit_code": 0,
                "codex_audit_path": ".woof/epics/E5/audit/codex-primary-run",
            }
        )
        + "\n"
    )
    (epic_dir / "check-result.json").write_text(
        json.dumps(
            {
                "ok": False,
                "stage": 5,
                "epic_id": 5,
                "work_unit_id": "S1",
                "triggered_by": ["check_1_quality_gates"],
                "checks": [
                    {
                        "id": "check_1_quality_gates",
                        "ok": False,
                        "severity": "blocker",
                        "summary": "quality gate failed",
                        "evidence": "just test exited 1",
                        "paths": [],
                        "command": "just test",
                        "exit_code": 1,
                    }
                ],
            }
        )
        + "\n"
    )


def test_preflight_passes_with_mocked_prerequisites(tmp_path: Path, run_woof) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)
    _write_exe(bin_dir / "pyright", 'echo "pyright 1.1.1"\n')
    _write_exe(
        bin_dir / "tree-sitter",
        """\
if [ "$1" = "--version" ]; then
  echo "tree-sitter 0.23.0"
  exit 0
fi
if [ "$1" = "parse" ]; then
  echo "(module)"
  exit 0
fi
echo "unexpected tree-sitter $*" >&2
exit 2
""",
    )

    _write_ready_project(
        tmp_path,
        prerequisites="""\
[infra]
just = "1.0+"
git = "2.30+"
gh = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "github"
repo = "example/project"

[indexing.tree-sitter]
cli = "0.22+"
grammars = ["python"]

[lsp]
languages = ["python"]
""",
        agents=AGENTS_WITH_EXECUTION_OVERLAY,
        quality_gates="""\
[gates.test]
command = "just test"
timeout_seconds = 30
""",
    )

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    assert {finding["id"] for finding in payload["findings"]} >= {
        "woof.install",
        "config.policy",
        "config.prerequisites",
        "config.agents",
        "policy.delivery",
        "policy.verification",
        "policy.run_profile",
        "policy.run_profile.producer",
        "policy.run_profile.reviewer",
        "policy.check_floor",
        "policy.cartography_floor",
        "policy.run_profile.producer.route",
        "policy.run_profile.reviewer.route",
        "github.repo",
        "lsp.python.binary",
        "tree-sitter.python",
        "quality-gates.test",
    }
    producer_route = next(
        finding
        for finding in payload["findings"]
        if finding["id"] == "policy.run_profile.producer.route"
    )
    assert "runtime=trusted-local" in producer_route["detail"]
    assert producer_route["required"] == (
        "harness plus registry-resolved model, effort, and runtime-mode disclosure"
    )
    assert producer_route["notes"] == [
        "trusted-local runtime: Woof dispatches subscription CLIs through tmux_harness; "
        "commit safety is enforced through deterministic checks, reviewer critique, human gates, "
        "transaction manifests, and commit decisions"
    ]
    reviewer_route = next(
        finding
        for finding in payload["findings"]
        if finding["id"] == "policy.run_profile.reviewer.route"
    )
    assert reviewer_route["ok"] is True
    assert "harness=claude" in reviewer_route["detail"]


def test_profile_a_policy_requires_worktree_root() -> None:
    from woof.cli.preflight import _check_policy_delivery

    policy = json.loads(
        json.dumps(
            {
                "delivery": {
                    "profile": "A",
                    "repo_root": ".",
                    "toolchain_root": ".",
                    "base_branch": "main",
                },
                "profiles": {
                    "A": {
                        "github_repo": "example/project",
                        "ready_label": "ready",
                        "merge_path_groups": [],
                    }
                },
            }
        )
    )

    finding = _check_policy_delivery(policy)

    assert finding.ok is False
    assert "profiles.A.worktree must be declared" in finding.detail


def test_deploy_aware_profile_a_preflight_requires_terminal_deploy_checks() -> None:
    from woof.cli.preflight import _check_policy_delivery

    policy = _load_policy_toml(
        PROFILE_A_POLICY.replace('terminal_deploy_checks = ["Deploy"]\n', "")
    )
    policy["drain"]["merge_after_ready_pr"] = True

    finding = _check_policy_delivery(policy)

    assert finding.ok is False
    assert "profiles.A.terminal_deploy_checks must list at least one check" in finding.detail


def test_profile_a_worktree_preflight_validates_ready_units(tmp_path: Path) -> None:
    from woof.cli.preflight import _check_profile_a_worktrees

    _init_repo(tmp_path)
    (tmp_path / ".woof" / "epics" / "E1").mkdir(parents=True)
    (tmp_path / ".woof" / "epics" / "E1" / "plan.json").write_text(
        json.dumps(
            {
                "epic_id": 1,
                "goal": "Validate worktrees.",
                "work_units": [
                    {
                        "id": "S1",
                        "title": "Ready unit",
                        "summary": "Ready for production.",
                        "paths": ["src/a.py"],
                        "deps": [],
                        "tests": {"count": 1, "types": ["unit"]},
                        "state": "pending",
                    },
                    {
                        "id": "S2",
                        "title": "Blocked unit",
                        "summary": "Waits on S1.",
                        "paths": ["src/b.py"],
                        "deps": ["S1"],
                        "tests": {"count": 1, "types": ["unit"]},
                        "state": "pending",
                    },
                ],
            }
        )
        + "\n"
    )
    worktree_root = tmp_path / "worktrees"
    worktree_root.mkdir()
    _git(tmp_path, "worktree", "add", "-b", "S1", str(worktree_root / "S1"), "main")

    findings = _check_profile_a_worktrees(tmp_path, _load_policy_toml(PROFILE_A_POLICY))

    assert [finding.as_dict() for finding in findings] == [
        {
            "id": "profile_a.worktree.S1",
            "label": "Profile A worktree S1",
            "ok": True,
            "detail": f"{worktree_root / 'S1'} on branch S1",
            "required": "existing clean linked worktree on base or unit branch",
        }
    ]


def test_profile_a_worktree_preflight_fails_closed_on_anomalies(tmp_path: Path) -> None:
    from woof.cli.preflight import _check_profile_a_worktrees

    _init_repo(tmp_path)
    plan_dir = tmp_path / ".woof" / "work-unit-sets" / "set-a"
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.json").write_text(
        json.dumps(
            {
                "context": {"kind": "work_unit_set", "project_ref": "woof", "set_id": "set-a"},
                "goal": "Validate duplicate worktrees.",
                "work_units": [
                    {
                        "id": "S1",
                        "title": "First",
                        "summary": "First ready unit.",
                        "paths": ["src/a.py"],
                        "deps": [],
                        "tests": {"count": 1, "types": ["unit"]},
                        "state": "pending",
                    },
                    {
                        "id": "S2",
                        "title": "Second",
                        "summary": "Second ready unit.",
                        "paths": ["src/b.py"],
                        "deps": [],
                        "tests": {"count": 1, "types": ["unit"]},
                        "state": "pending",
                    },
                ],
            }
        )
        + "\n"
    )
    worktree_root = tmp_path / "worktrees"
    worktree_root.mkdir()
    _git(tmp_path, "worktree", "add", "-b", "S1", str(worktree_root / "shared"), "main")
    (plan_dir / "intake.json").write_text(
        json.dumps(
            {
                "worktrees": {
                    "derivation": "manifest_map",
                    "root": "worktrees",
                    "unit_paths": {"S1": "worktrees/shared", "S2": "worktrees/shared"},
                }
            }
        )
        + "\n"
    )
    policy = _load_policy_toml(
        PROFILE_A_POLICY.replace(
            'root = "worktrees"\n',
            'root = "worktrees"\nderivation = "manifest_map"\n',
        )
    )

    findings = _check_profile_a_worktrees(tmp_path, policy)

    assert [finding.id for finding in findings] == [
        "profile_a.worktree.S1",
        "profile_a.worktree.S2",
        "profile_a.worktree.paths",
    ]
    assert findings[0].ok is True
    assert findings[1].ok is False
    assert "branch 'S1' is not one of: S2, main" in findings[1].detail
    assert findings[2].ok is False
    assert "S1 and S2 both resolve to" in findings[2].detail


def test_profile_a_worktree_preflight_fails_closed_when_worktree_absent(
    tmp_path: Path,
) -> None:
    from woof.cli.preflight import _check_profile_a_worktrees

    _init_repo(tmp_path)
    plan_dir = tmp_path / ".woof" / "work-unit-sets" / "set-a"
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.json").write_text(
        json.dumps(
            {
                "context": {"kind": "work_unit_set", "project_ref": "woof", "set_id": "set-a"},
                "goal": "Validate missing worktree.",
                "work_units": [
                    {
                        "id": "S1",
                        "title": "Missing worktree",
                        "summary": "Ready unit without a provisioned worktree.",
                        "paths": ["src/a.py"],
                        "deps": [],
                        "tests": {"count": 1, "types": ["unit"]},
                        "state": "pending",
                    }
                ],
            }
        )
        + "\n"
    )

    findings = _check_profile_a_worktrees(tmp_path, _load_policy_toml(PROFILE_A_POLICY))

    assert [finding.id for finding in findings] == ["profile_a.worktree.S1"]
    assert findings[0].ok is False
    assert "does not exist; Woof will not create it" in findings[0].detail


def test_profile_a_worktree_preflight_fails_closed_for_foreign_repo_worktree(
    tmp_path: Path,
) -> None:
    from woof.cli.preflight import _check_profile_a_worktrees

    _init_repo(tmp_path)
    foreign_repo = tmp_path / "foreign"
    foreign_repo.mkdir()
    _init_repo(foreign_repo)
    plan_dir = tmp_path / ".woof" / "work-unit-sets" / "set-a"
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.json").write_text(
        json.dumps(
            {
                "context": {"kind": "work_unit_set", "project_ref": "woof", "set_id": "set-a"},
                "goal": "Validate foreign worktree.",
                "work_units": [
                    {
                        "id": "S1",
                        "title": "Foreign repo",
                        "summary": "Ready unit mapped to another repository.",
                        "paths": ["src/a.py"],
                        "deps": [],
                        "tests": {"count": 1, "types": ["unit"]},
                        "state": "pending",
                    }
                ],
            }
        )
        + "\n"
    )
    (plan_dir / "intake.json").write_text(
        json.dumps(
            {
                "worktrees": {
                    "derivation": "manifest_map",
                    "root": "worktrees",
                    "unit_paths": {"S1": "foreign"},
                }
            }
        )
        + "\n"
    )
    policy = _load_policy_toml(
        PROFILE_A_POLICY.replace(
            'root = "worktrees"\n',
            'root = "worktrees"\nderivation = "manifest_map"\n',
        )
    )

    findings = _check_profile_a_worktrees(tmp_path, policy)

    assert [finding.id for finding in findings] == ["profile_a.worktree.S1"]
    assert findings[0].ok is False
    assert f"{foreign_repo} is not a linked worktree of {tmp_path}" in findings[0].detail


def test_profile_a_worktree_preflight_fails_closed_for_dirty_linked_worktree(
    tmp_path: Path,
) -> None:
    from woof.cli.preflight import _check_profile_a_worktrees

    _init_repo(tmp_path)
    plan_dir = tmp_path / ".woof" / "work-unit-sets" / "set-a"
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.json").write_text(
        json.dumps(
            {
                "context": {"kind": "work_unit_set", "project_ref": "woof", "set_id": "set-a"},
                "goal": "Validate dirty worktree.",
                "work_units": [
                    {
                        "id": "S1",
                        "title": "Dirty worktree",
                        "summary": "Ready unit mapped to a dirty linked worktree.",
                        "paths": ["src/a.py"],
                        "deps": [],
                        "tests": {"count": 1, "types": ["unit"]},
                        "state": "pending",
                    }
                ],
            }
        )
        + "\n"
    )
    worktree_root = tmp_path / "worktrees"
    worktree_root.mkdir()
    worktree_path = worktree_root / "S1"
    _git(tmp_path, "worktree", "add", "-b", "S1", str(worktree_path), "main")
    (worktree_path / "untracked.txt").write_text("dirty\n")

    findings = _check_profile_a_worktrees(tmp_path, _load_policy_toml(PROFILE_A_POLICY))

    assert [finding.id for finding in findings] == ["profile_a.worktree.S1"]
    assert findings[0].ok is False
    assert f"{worktree_path} is dirty" in findings[0].detail


def test_preflight_fails_for_missing_policy_producer_slot(tmp_path: Path, run_woof) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)

    _write_ready_project(
        tmp_path,
        prerequisites="""\
[infra]
just = "any"
git = "any"
gh = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "github"
repo = "example/project"
""",
        policy=STANDARD_POLICY.replace(
            """\
[run_profiles.default.producer]
harness = "codex"
model = "gpt-5.5"
effort = "xhigh"

""",
            "",
        ),
    )

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    finding_ids = {f["id"] for f in payload["findings"]}
    assert "policy.run_profile.producer" in finding_ids
    assert "policy.run_profile.producer.route" in finding_ids
    producer = next(f for f in payload["findings"] if f["id"] == "policy.run_profile.producer")
    assert producer["ok"] is False
    route = next(f for f in payload["findings"] if f["id"] == "policy.run_profile.producer.route")
    assert route["ok"] is False


def test_preflight_passes_with_policy_run_profile_resolved(tmp_path: Path, run_woof) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)

    _write_ready_project(
        tmp_path,
        prerequisites="""\
[infra]
just = "any"
git = "any"
gh = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "github"
repo = "example/project"
""",
        agents=AGENTS_WITH_EXECUTION_OVERLAY,
    )

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout)
    finding_ids = {f["id"] for f in payload["findings"]}
    expected_ids = {
        "policy.run_profile.producer.route",
        "policy.run_profile.reviewer.route",
    }
    assert expected_ids <= finding_ids
    for fid in expected_ids:
        finding = next(f for f in payload["findings"] if f["id"] == fid)
        assert finding["ok"] is True, f"{fid} should pass: {finding['detail']}"


def test_preflight_agents_template_matches_init_template() -> None:
    from woof.cli.init import AGENTS_TEMPLATE
    from woof.cli.preflight import _agents_template

    assert _agents_template() == f"Create .woof/agents.toml, for example:\n{AGENTS_TEMPLATE}"


def test_preflight_resolves_missing_policy_effort_to_harness_default(
    tmp_path: Path, run_woof
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)

    _write_ready_project(
        tmp_path,
        prerequisites="""\
[infra]
just = "any"
git = "any"
gh = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "github"
repo = "example/project"
""",
        policy=STANDARD_POLICY.replace('effort = "xhigh"\n', ""),
    )

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout)
    producer = next(
        finding
        for finding in payload["findings"]
        if finding["id"] == "policy.run_profile.producer.route"
    )
    assert producer["ok"] is True
    assert "effort=medium" in producer["detail"]


def test_preflight_allows_missing_agents_toml(tmp_path: Path, run_woof) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)

    _write_ready_project(
        tmp_path,
        prerequisites="""\
[infra]
just = "any"
git = "any"
gh = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "github"
repo = "example/project"
""",
        agents=None,
    )

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    assert "config.agents" not in {finding["id"] for finding in payload["findings"]}


def test_preflight_requires_policy_toml(tmp_path: Path, run_woof) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)

    _write_ready_project(
        tmp_path,
        prerequisites="""\
[infra]
just = "any"
git = "any"
gh = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "github"
repo = "example/project"
""",
        policy=None,
    )

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    policy = next(finding for finding in payload["findings"] if finding["id"] == "policy.config")
    assert policy["ok"] is False
    assert "policy.toml" in policy["detail"]


def test_preflight_runs_host_and_server_checks(tmp_path: Path, run_woof) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)
    _write_exe(bin_dir / "host-ready", "exit 0\n")
    _write_exe(bin_dir / "server-ready", "exit 0\n")
    if sys.platform.startswith("linux"):
        platform = "linux"
    elif sys.platform == "darwin":
        platform = "darwin"
    else:
        platform = "windows"

    _write_ready_project(
        tmp_path,
        prerequisites=f"""\
[infra]
just = "any"
git = "any"
gh = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "github"
repo = "example/project"

[host]
platforms = ["{platform}"]

[host.checks.project]
command = "host-ready"
required = "project host tooling ready"

[servers.dev]
command = "server-ready"
required = "local dev server ready"
""",
    )

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout)
    assert {
        "host.platform",
        "host.project",
        "servers.dev",
    } <= {finding["id"] for finding in payload["findings"]}


def test_preflight_reports_missing_prerequisites_template(tmp_path: Path, run_woof) -> None:
    (tmp_path / ".woof").mkdir()

    proc = run_woof("preflight", "--project-root", str(tmp_path), env=os.environ.copy())

    assert proc.returncode == 1
    assert "prerequisites.toml" in proc.stdout
    assert 'repo = "<replace>/<replace>"' in proc.stdout


def test_preflight_fails_for_missing_declared_command(tmp_path: Path, run_woof) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)
    (bin_dir / "codex").unlink()

    _write_ready_project(
        tmp_path,
        prerequisites="""\
[infra]
just = "any"
git = "any"
gh = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "github"
repo = "example/project"
""",
    )

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    codex = next(finding for finding in payload["findings"] if finding["id"] == "commands.codex")
    assert codex["ok"] is False
    assert "codex not found" in codex["detail"]


def test_preflight_checks_declared_lsp_plugin(tmp_path: Path, run_woof) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)
    _write_exe(bin_dir / "pyright", 'echo "pyright 1.1.1"\n')
    _write_exe(bin_dir / "claude", 'echo "pyright-lsp@claude-plugins-official"\n')

    tool_root = tmp_path / "tool"
    (tool_root / "languages").mkdir(parents=True)
    (tool_root / "schemas").symlink_to(REPO_ROOT / "schemas")
    (tool_root / "languages" / "python.toml").write_text(
        """\
[lsp]
binary = "pyright"
binary_install = "npm install -g pyright"
plugin = "pyright-lsp@claude-plugins-official"
plugin_install = "claude plugin install pyright-lsp@claude-plugins-official"

[tree-sitter]
grammar_install = "npm install -g tree-sitter-python"
verify_snippet = "def f(): pass"
verify_scope = "source.python"
"""
    )
    _write_ready_project(
        tmp_path,
        prerequisites="""\
[infra]
just = "any"
git = "any"
gh = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "github"
repo = "example/project"

[lsp]
languages = ["python"]
""",
    )

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir, {"WOOF_TOOL_ROOT": str(tool_root)}),
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout)
    plugin = next(
        finding for finding in payload["findings"] if finding["id"] == "lsp.python.plugin"
    )
    assert plugin["ok"] is True


def test_preflight_reuses_floor_cache_until_forced(tmp_path: Path, run_woof) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)
    _write_exe(bin_dir / "pyright", 'echo "pyright 1.1.1"\n')

    _write_ready_project(
        tmp_path,
        prerequisites="""\
[infra]
just = "any"
git = "any"
gh = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "github"
repo = "example/project"

[lsp]
languages = ["python"]
""",
    )

    first = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert first.returncode == 0, first.stderr + first.stdout
    assert (tmp_path / ".woof" / ".preflight-floor").is_file()
    assert (tmp_path / ".woof" / ".preflight-runtime").is_file()

    (bin_dir / "pyright").unlink()
    cached = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert cached.returncode == 0, cached.stderr + cached.stdout
    cached_payload = json.loads(cached.stdout)
    lsp = next(
        finding for finding in cached_payload["findings"] if finding["id"] == "lsp.python.binary"
    )
    assert lsp["ok"] is True

    forced = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        "--force",
        env=_env_with_path(bin_dir),
    )

    assert forced.returncode == 1
    forced_payload = json.loads(forced.stdout)
    forced_lsp = next(
        finding for finding in forced_payload["findings"] if finding["id"] == "lsp.python.binary"
    )
    assert forced_lsp["ok"] is False
    assert "pyright not found" in forced_lsp["detail"]

    after_failed_force = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert after_failed_force.returncode == 1


def test_preflight_rechecks_stale_runtime_cache(tmp_path: Path, run_woof) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)

    _write_ready_project(
        tmp_path,
        prerequisites="""\
[infra]
just = "any"
git = "any"
gh = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "github"
repo = "example/project"
""",
    )

    first = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert first.returncode == 0, first.stderr + first.stdout
    runtime_cache = tmp_path / ".woof" / ".preflight-runtime"
    runtime_payload = json.loads(runtime_cache.read_text())
    runtime_payload["verified_at"] = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    runtime_cache.write_text(json.dumps(runtime_payload))
    _write_exe(
        bin_dir / "gh",
        """\
echo "expired gh auth" >&2
exit 42
""",
    )

    stale = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert stale.returncode == 1
    stale_payload = json.loads(stale.stdout)
    rate_limit = next(
        finding for finding in stale_payload["findings"] if finding["id"] == "github.rate_limit"
    )
    assert rate_limit["ok"] is False
    assert "expired gh auth" in rate_limit["detail"]


def test_preflight_passes_adapter_auth_when_env_keys_set(tmp_path: Path, run_woof) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)

    _write_ready_project(
        tmp_path,
        prerequisites="""\
[infra]
just = "any"
git = "any"
gh = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "github"
repo = "example/project"
""",
    )

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout)
    primary = next(f for f in payload["findings"] if f["id"] == "policy.run_profile.producer.auth")
    reviewer = next(f for f in payload["findings"] if f["id"] == "policy.run_profile.reviewer.auth")
    assert primary["ok"] is True
    assert "ANTHROPIC_API_KEY" not in primary["detail"]
    assert "OPENAI_API_KEY" in primary["detail"]
    assert reviewer["ok"] is True
    assert "ANTHROPIC_API_KEY" in reviewer["detail"]


def test_preflight_passes_adapter_auth_when_credential_files_present(
    tmp_path: Path, run_woof
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)

    claude_home = tmp_path / "claude-home"
    codex_home = tmp_path / "codex-home"
    claude_home.mkdir()
    codex_home.mkdir()
    (claude_home / ".credentials.json").write_text("{}\n")
    (codex_home / "auth.json").write_text("{}\n")

    _write_ready_project(
        tmp_path,
        prerequisites="""\
[infra]
just = "any"
git = "any"
gh = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "github"
repo = "example/project"
""",
    )

    env = _env_with_path(
        bin_dir,
        {
            "CLAUDE_CONFIG_DIR": str(claude_home),
            "CODEX_HOME": str(codex_home),
        },
    )
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=env,
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout)
    primary = next(f for f in payload["findings"] if f["id"] == "policy.run_profile.producer.auth")
    reviewer = next(f for f in payload["findings"] if f["id"] == "policy.run_profile.reviewer.auth")
    assert primary["ok"] is True
    assert str(codex_home / "auth.json") in primary["detail"]
    assert reviewer["ok"] is True
    assert str(claude_home / ".credentials.json") in reviewer["detail"]


def test_preflight_fails_when_adapter_auth_marker_missing(tmp_path: Path, run_woof) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)

    empty_home = tmp_path / "empty-home"
    empty_home.mkdir()

    _write_ready_project(
        tmp_path,
        prerequisites="""\
[infra]
just = "any"
git = "any"
gh = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "github"
repo = "example/project"
""",
    )

    env = _env_with_path(
        bin_dir,
        {
            "CLAUDE_CONFIG_DIR": str(empty_home),
            "CODEX_HOME": str(empty_home),
        },
    )
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=env,
    )

    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    primary = next(f for f in payload["findings"] if f["id"] == "policy.run_profile.producer.auth")
    reviewer = next(f for f in payload["findings"] if f["id"] == "policy.run_profile.reviewer.auth")
    assert primary["ok"] is False
    assert "codex dispatch will fail" in primary["detail"]
    assert primary["install"] == "codex login"
    assert reviewer["ok"] is False
    assert "claude dispatch will fail" in reviewer["detail"]
    assert reviewer["install"] == "claude /login"


def test_preflight_flags_non_executable_cartography_script(tmp_path: Path, run_woof) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)

    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    cartography = scripts_dir / "refresh-cartography"
    cartography.write_text("#!/usr/bin/env sh\necho cartography\n")
    cartography.chmod(0o644)

    _write_project(
        tmp_path,
        prerequisites=CARTOGRAPHY_PREREQS,
    )
    _write_cartography(tmp_path, script=False)

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    cart = next(f for f in payload["findings"] if f["id"] == "cartography.script")
    assert cart["ok"] is False
    assert "not executable" in cart["detail"]
    assert cart["install"] == f"chmod +x {cartography}"

    cartography.chmod(0o755)
    forced = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        "--force",
        env=_env_with_path(bin_dir),
    )
    assert forced.returncode == 0, forced.stderr + forced.stdout
    payload = json.loads(forced.stdout)
    cart = next(f for f in payload["findings"] if f["id"] == "cartography.script")
    assert cart["ok"] is True


def test_preflight_fails_with_onboarding_error_when_cartography_block_absent(
    tmp_path: Path, run_woof
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)

    _write_project(
        tmp_path,
        prerequisites="""\
[infra]
just = "any"
git = "any"
gh = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "github"
repo = "example/project"
""",
    )

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    floor = next(f for f in payload["findings"] if f["id"] == "policy.cartography_floor")
    assert floor["ok"] is False
    assert "requires .woof/prerequisites.toml [cartography]" in floor["detail"]
    contract = next(f for f in payload["findings"] if f["id"] == "cartography.contract")
    assert contract["ok"] is False
    assert "no [cartography] block" in contract["detail"]
    assert "/woof setup" in contract["install"]
    assert "/woof map-codebase" in contract["install"]
    assert "skills/woof/references/setup.md" in contract["install"]
    assert "skills/woof/references/map-codebase.md" in contract["install"]


def test_preflight_allows_no_cartography_without_cartography_prerequisites(
    tmp_path: Path, run_woof
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)

    _write_project(
        tmp_path,
        policy=POLICY_NO_CARTOGRAPHY,
        prerequisites="""\
[infra]
just = "any"
git = "any"
gh = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "github"
repo = "example/project"
""",
    )

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout)
    by_id = {f["id"]: f for f in payload["findings"]}
    assert by_id["policy.cartography_floor"]["detail"] == "floor=none"
    assert "cartography.contract" not in by_id
    assert "cartography.script" not in by_id
    assert "cartography.mechanical" not in by_id


def _run_cartography_preflight(tmp_path: Path, run_woof):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)
    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )
    return proc, json.loads(proc.stdout)


def test_preflight_passes_with_declared_cartography(tmp_path: Path, run_woof) -> None:
    _write_project(tmp_path, prerequisites=CARTOGRAPHY_PREREQS)
    _write_cartography(tmp_path)

    proc, payload = _run_cartography_preflight(tmp_path, run_woof)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    by_id = {f["id"]: f for f in payload["findings"]}
    for fid in (
        "cartography.script",
        "cartography.target_architecture",
        "cartography.principles",
        "cartography.mechanical",
    ):
        assert by_id[fid]["ok"] is True, by_id[fid]


def test_preflight_fails_for_missing_cartography_script_when_declared(
    tmp_path: Path, run_woof
) -> None:
    _write_project(tmp_path, prerequisites=CARTOGRAPHY_PREREQS)
    _write_cartography(tmp_path, script=False)

    proc, payload = _run_cartography_preflight(tmp_path, run_woof)

    assert proc.returncode == 1
    script = next(f for f in payload["findings"] if f["id"] == "cartography.script")
    assert script["ok"] is False
    assert "not found" in script["detail"]
    assert "map-codebase" in script["install"]


def test_preflight_fails_for_missing_cartography_design_doc(tmp_path: Path, run_woof) -> None:
    _write_project(tmp_path, prerequisites=CARTOGRAPHY_PREREQS)
    _write_cartography(tmp_path, target=None)

    proc, payload = _run_cartography_preflight(tmp_path, run_woof)

    assert proc.returncode == 1
    doc = next(f for f in payload["findings"] if f["id"] == "cartography.target_architecture")
    assert doc["ok"] is False
    assert "TARGET-ARCHITECTURE.md not found" in doc["detail"]
    assert "map-codebase" in doc["install"]


def test_preflight_fails_for_stub_marker_design_doc(tmp_path: Path, run_woof) -> None:
    _write_project(tmp_path, prerequisites=CARTOGRAPHY_PREREQS)
    _write_cartography(tmp_path, target="<!-- woof:stub -->\n" + DESIGN_DOC_BODY)

    proc, payload = _run_cartography_preflight(tmp_path, run_woof)

    assert proc.returncode == 1
    doc = next(f for f in payload["findings"] if f["id"] == "cartography.target_architecture")
    assert doc["ok"] is False
    assert "stub marker" in doc["detail"]


def test_preflight_fails_for_short_design_doc(tmp_path: Path, run_woof) -> None:
    _write_project(tmp_path, prerequisites=CARTOGRAPHY_PREREQS)
    _write_cartography(tmp_path, target="Too short.\n")

    proc, payload = _run_cartography_preflight(tmp_path, run_woof)

    assert proc.returncode == 1
    doc = next(f for f in payload["findings"] if f["id"] == "cartography.target_architecture")
    assert doc["ok"] is False
    assert "is a stub" in doc["detail"]
    assert "40-char floor" in doc["detail"]


def test_preflight_accepts_short_design_doc_marked_complete(tmp_path: Path, run_woof) -> None:
    _write_project(tmp_path, prerequisites=CARTOGRAPHY_PREREQS)
    _write_cartography(tmp_path, target="---\nstatus: complete\n---\nTiny but intentional.\n")

    proc, payload = _run_cartography_preflight(tmp_path, run_woof)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    doc = next(f for f in payload["findings"] if f["id"] == "cartography.target_architecture")
    assert doc["ok"] is True
    assert "marked complete" in doc["detail"]


def test_preflight_fails_for_missing_mechanical_layer(tmp_path: Path, run_woof) -> None:
    _write_project(tmp_path, prerequisites=CARTOGRAPHY_PREREQS)
    _write_cartography(tmp_path, mechanical=False)

    proc, payload = _run_cartography_preflight(tmp_path, run_woof)

    assert proc.returncode == 1
    mech = next(f for f in payload["findings"] if f["id"] == "cartography.mechanical")
    assert mech["ok"] is False
    assert "missing mechanical file(s)" in mech["detail"]
    assert "files.txt" in mech["detail"]


def _write_freshness(root: Path, payload: dict | str) -> None:
    """Overwrite the mechanical freshness.json with a chosen stamp (or raw text)."""

    path = root / ".woof" / "codebase" / "freshness.json"
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload) + "\n")


def test_preflight_warns_for_stale_cartography_freshness(tmp_path: Path, run_woof) -> None:
    # Models the production failure mode: the post-commit hook freezes age_s at 0
    # on every write, so a stamp only ages once commits stop. ts is authoritative
    # -- a deep-past ts is robustly stale regardless of the host wall-clock, and
    # the frozen age_s = 0 must NOT mask it. Default staleness floor is 168h.
    _write_project(tmp_path, prerequisites=CARTOGRAPHY_PREREQS)
    _write_cartography(tmp_path)
    _write_freshness(
        tmp_path,
        {
            "ts": "2020-01-01T00:00:00Z",
            "git_ref": "abc",
            "age_s": 0,
            "generator_version": 1,
        },
    )

    proc, payload = _run_cartography_preflight(tmp_path, run_woof)

    # A stale stamp warns but does not fail preflight.
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert payload["ok"] is True
    assert payload["warnings"] == 1
    fresh = next(f for f in payload["findings"] if f["id"] == "cartography.freshness")
    assert fresh["ok"] is True
    assert fresh["warn"] is True
    assert "staleness floor" in fresh["detail"]
    # The warning carries the refresh prompt.
    assert any("./scripts/refresh-cartography" in note for note in fresh["notes"])


def test_preflight_does_not_warn_for_fresh_cartography_freshness(tmp_path: Path, run_woof) -> None:
    _write_project(tmp_path, prerequisites=CARTOGRAPHY_PREREQS)
    _write_cartography(tmp_path)  # default stamp has ts = now() (fresh)

    proc, payload = _run_cartography_preflight(tmp_path, run_woof)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert payload["warnings"] == 0
    fresh = next(f for f in payload["findings"] if f["id"] == "cartography.freshness")
    assert fresh["ok"] is True
    assert fresh.get("warn") is not True
    assert "within the" in fresh["detail"]


def test_preflight_warns_for_stale_freshness_via_age_s_fallback(tmp_path: Path, run_woof) -> None:
    # No ts: age derives from the deterministic age_s fallback. A test injects a
    # precise age this way without coupling to wall-clock.
    _write_project(tmp_path, prerequisites=CARTOGRAPHY_PREREQS)
    _write_cartography(tmp_path)
    _write_freshness(tmp_path, {"git_ref": "abc", "age_s": 169 * 3600, "generator_version": 1})

    proc, payload = _run_cartography_preflight(tmp_path, run_woof)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    fresh = next(f for f in payload["findings"] if f["id"] == "cartography.freshness")
    assert fresh["warn"] is True
    assert any("refresh-cartography" in note for note in fresh["notes"])


def test_preflight_does_not_warn_for_fresh_freshness_via_age_s_fallback(
    tmp_path: Path, run_woof
) -> None:
    # No ts: the deterministic age_s fallback also drives the fresh verdict, so
    # the fallback path does not over-warn.
    _write_project(tmp_path, prerequisites=CARTOGRAPHY_PREREQS)
    _write_cartography(tmp_path)
    _write_freshness(tmp_path, {"git_ref": "abc", "age_s": 1, "generator_version": 1})

    proc, payload = _run_cartography_preflight(tmp_path, run_woof)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert payload["warnings"] == 0
    fresh = next(f for f in payload["findings"] if f["id"] == "cartography.freshness")
    assert fresh["ok"] is True
    assert fresh.get("warn") is not True
    assert "within the" in fresh["detail"]


def test_preflight_warns_for_malformed_cartography_freshness(tmp_path: Path, run_woof) -> None:
    # An unparseable stamp is non-blocking: presence, not readability, is the
    # blocking concern (the mechanical check already covers presence).
    _write_project(tmp_path, prerequisites=CARTOGRAPHY_PREREQS)
    _write_cartography(tmp_path)
    _write_freshness(tmp_path, "{ not valid json")

    proc, payload = _run_cartography_preflight(tmp_path, run_woof)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    fresh = next(f for f in payload["findings"] if f["id"] == "cartography.freshness")
    assert fresh["ok"] is True
    assert fresh["warn"] is True
    assert "could not be read" in fresh["detail"]
    assert any("refresh-cartography" in note for note in fresh["notes"])


def test_preflight_json_reports_operator_state_for_current_epic(
    tmp_path: Path,
    run_woof,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)
    _write_ready_project(
        tmp_path,
        prerequisites="""\
[infra]
just = "any"
git = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "local"
""",
    )
    _write_current_epic_state(tmp_path)

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout)
    state = payload["operator_state"]
    assert state["current_epic"]["epic_id"] == 5
    assert state["runtime_policy"]["mode"] == "trusted-local"
    assert state["dispatch_routes"]["roles"]["producer"]["adapter"] == "codex"
    assert state["epic"]["next"] == {
        "node": "human_review",
        "work_unit_id": None,
        "reason": "gate_open",
    }
    assert state["epic"]["next_action"]["command"] == "woof wf --epic 5 --resolve <decision>"
    assert state["epic"]["gate"]["cause"] == "check_1_quality_gates"
    assert state["epic"]["checks"]["failed_checks"][0]["summary"] == "quality gate failed"
    assert state["epic"]["audit_pointers"]["latest_codex_audit_path"] == (
        ".woof/epics/E5/audit/codex-primary-run"
    )


def test_preflight_text_reports_operator_state_for_current_epic(
    tmp_path: Path,
    run_woof,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)
    _write_ready_project(
        tmp_path,
        prerequisites="""\
[infra]
just = "any"
git = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "local"
""",
    )
    _write_current_epic_state(tmp_path)

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        env=_env_with_path(bin_dir),
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "Operator state:" in proc.stdout
    assert "current_epic: E5 selected=true valid=true epic_dir_exists=true" in proc.stdout
    assert "runtime_policy: trusted-local" in proc.stdout
    assert "producer: adapter=codex model=gpt-5.5 effort=xhigh" in proc.stdout
    assert "next_action: resolve_gate command=woof wf --epic 5 --resolve <decision>" in proc.stdout
    assert "gate: open type=work_unit_gate work_unit=S1 cause=check_1_quality_gates" in proc.stdout
    assert "checks: FAIL total=1 failed=1 triggered_by=check_1_quality_gates" in proc.stdout
    assert "audit_pointers: epic_jsonl=.woof/epics/E5/epic.jsonl" in proc.stdout


def test_preflight_flags_secret_in_committed_cartography_doc(tmp_path: Path, run_woof) -> None:
    _write_project(
        tmp_path,
        prerequisites=CARTOGRAPHY_PREREQS,
    )
    _write_cartography(tmp_path)
    leaked = tmp_path / ".woof" / "codebase" / "CONCERNS.md"
    leaked.write_text(
        "# Concerns\n\nThe staging deploy hardcodes aws = AKIA1234567890ABCDEF in the script.\n"
    )

    proc = run_woof("preflight", "--project-root", str(tmp_path), "--format", "json")

    assert proc.returncode != 0
    by_id = {finding["id"]: finding for finding in json.loads(proc.stdout)["findings"]}
    assert "cartography.secrets.CONCERNS" in by_id
    finding = by_id["cartography.secrets.CONCERNS"]
    assert finding["ok"] is False
    assert "aws_access_key" in finding["detail"]
    # The matched value must never be echoed into preflight output.
    assert "AKIA1234567890ABCDEF" not in proc.stdout


def test_preflight_secret_scan_passes_on_clean_cartography(tmp_path: Path, run_woof) -> None:
    _write_project(
        tmp_path,
        prerequisites=CARTOGRAPHY_PREREQS,
    )
    _write_cartography(tmp_path)

    proc = run_woof("preflight", "--project-root", str(tmp_path), "--format", "json")

    by_id = {finding["id"]: finding for finding in json.loads(proc.stdout)["findings"]}
    assert by_id["cartography.secrets"]["ok"] is True


# --- cartography.ctags check (ADR-004 conformance, E16) ----------------------

CARTOGRAPHY_PREREQS_WITH_LANGUAGES = """\
[infra]
just = "any"
git = "any"
gh = "any"

[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[tracker]
kind = "github"
repo = "example/project"

[cartography]
summary_min_chars = 40
languages = ["python"]
"""


def _env_with_path_no_ctags(tmp_path: Path, bin_dir: Path) -> dict[str, str]:
    """Like _env_with_path but with ctags excluded from the resolved PATH.

    On systems where sh and ctags share a directory (e.g. /usr/bin), a plain
    _env_with_path would include that directory and shutil.which('ctags') inside
    the preflight subprocess would succeed. This helper shadows sh into a private
    dir so PATH can include sh without including ctags.
    """
    uv = shutil.which("uv")
    sh = shutil.which("sh")
    assert uv is not None
    assert sh is not None

    sh_shadow = tmp_path / "_sh_shadow"
    sh_shadow.mkdir(exist_ok=True)
    sh_link = sh_shadow / "sh"
    if not sh_link.exists():
        sh_link.symlink_to(sh)

    env = os.environ.copy()
    env["PATH"] = os.pathsep.join([str(bin_dir), str(Path(uv).parent), str(sh_shadow)])
    env.setdefault("ANTHROPIC_API_KEY", "stub-anthropic")
    env.setdefault("OPENAI_API_KEY", "stub-openai")
    return env


def test_preflight_ctags_finding_fails_when_ctags_not_universal(tmp_path: Path, run_woof) -> None:
    """cartography.ctags fails when ctags on PATH is not Universal Ctags (ADR-004)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)
    _write_exe(bin_dir / "ctags", 'echo "Exuberant Ctags 5.8"\n')

    _write_project(tmp_path, prerequisites=CARTOGRAPHY_PREREQS_WITH_LANGUAGES)
    _write_cartography(tmp_path)

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert proc.returncode == 1
    by_id = {f["id"]: f for f in json.loads(proc.stdout)["findings"]}
    assert "cartography.ctags" in by_id
    ctags_finding = by_id["cartography.ctags"]
    assert ctags_finding["ok"] is False
    assert "not Universal Ctags" in ctags_finding["detail"]
    assert "universal-ctags" in ctags_finding["install"]


def test_preflight_ctags_finding_fires_when_absent_and_languages_declared(
    tmp_path: Path, run_woof
) -> None:
    """cartography.ctags fails when languages declared and ctags absent from PATH (ADR-004)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)
    # Intentionally no ctags stub in bin_dir.

    _write_project(tmp_path, prerequisites=CARTOGRAPHY_PREREQS_WITH_LANGUAGES)
    _write_cartography(tmp_path)

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path_no_ctags(tmp_path, bin_dir),
    )

    assert proc.returncode == 1
    by_id = {f["id"]: f for f in json.loads(proc.stdout)["findings"]}
    assert "cartography.ctags" in by_id
    ctags_finding = by_id["cartography.ctags"]
    assert ctags_finding["ok"] is False
    assert "ctags not found" in ctags_finding["detail"]
    assert "universal-ctags" in ctags_finding["install"]


def test_preflight_ctags_finding_passes_when_ctags_present(tmp_path: Path, run_woof) -> None:
    """cartography.ctags passes when ctags is on PATH and languages are declared."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)
    _write_exe(bin_dir / "ctags", 'echo "Universal Ctags 6.0.0"\n')

    _write_project(tmp_path, prerequisites=CARTOGRAPHY_PREREQS_WITH_LANGUAGES)
    _write_cartography(tmp_path)

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    by_id = {f["id"]: f for f in json.loads(proc.stdout)["findings"]}
    assert "cartography.ctags" in by_id
    assert by_id["cartography.ctags"]["ok"] is True


def test_preflight_no_ctags_finding_when_no_languages_declared(tmp_path: Path, run_woof) -> None:
    """cartography.ctags is not emitted when [cartography].languages is absent."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_core_tools(bin_dir)
    # No ctags stub — but no languages declared either.

    _write_project(tmp_path, prerequisites=CARTOGRAPHY_PREREQS)
    _write_cartography(tmp_path)

    proc = run_woof(
        "preflight",
        "--project-root",
        str(tmp_path),
        "--format",
        "json",
        env=_env_with_path(bin_dir),
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    finding_ids = {f["id"] for f in json.loads(proc.stdout)["findings"]}
    assert "cartography.ctags" not in finding_ids


def test_first_time_setup_includes_ctags_prerequisite() -> None:
    """scripts/first-time-setup.sh must enforce Universal Ctags at the setup seam."""
    script = REPO_ROOT / "scripts" / "first-time-setup.sh"
    assert script.is_file(), f"{script} not found"
    content = script.read_text()
    assert "require_universal_ctags" in content, (
        "ctags check must use require_universal_ctags (validates Universal Ctags banner)"
    )

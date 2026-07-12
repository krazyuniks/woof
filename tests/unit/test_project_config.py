"""The single per-project config in the operator home (ADR-017)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import MINIMAL_PROJECT_CONFIG, write_project_config
from woof.project_config import (
    ProjectConfigError,
    load_project_config,
    load_raw_project_config,
)


def test_a_missing_project_config_is_a_hard_error_naming_path_and_init(
    woof_home: Path,
) -> None:
    with pytest.raises(ProjectConfigError) as excinfo:
        load_project_config("absent-project")

    message = str(excinfo.value)
    expected = woof_home / "config" / "projects" / "absent-project.toml"
    assert str(expected) in message
    assert "woof init --project absent-project" in message


def test_no_in_repo_fallback_exists(woof_home: Path, tmp_path: Path) -> None:
    """A `.woof/` directory beside the checkout must never satisfy the loader."""

    legacy = tmp_path / ".woof"
    legacy.mkdir()
    (legacy / "policy.toml").write_text(MINIMAL_PROJECT_CONFIG, encoding="utf-8")

    with pytest.raises(ProjectConfigError):
        load_project_config("absent-project")


def test_loads_every_collapsed_section_into_frozen_dataclasses(woof_home: Path) -> None:
    write_project_config("demo", MINIMAL_PROJECT_CONFIG)
    config = load_project_config("demo")

    assert config.key == "demo"
    assert config.source == woof_home / "config" / "projects" / "demo.toml"

    assert config.delivery.profile == "B"
    assert config.delivery.base_branch == "main"
    assert config.profile_b is not None and config.profile_b.commit is True

    assert config.verification.command == "just check"
    assert config.verification.timeout_seconds == 600

    assert config.run_profile.name == "default"
    assert config.run_profile.producer.harness == "codex"
    assert config.run_profile.reviewer.harness == "claude"

    assert "quality-gates" in config.checks.floor
    assert config.checks.review_size is not None
    assert config.checks.review_size.max_non_generated_changed_lines == 500

    assert config.cartography.floor == "structural"
    assert config.cartography.staleness_floor_hours == 168
    assert config.cartography.languages == ("python",)

    assert config.drain.stop_when_no_eligible_units is True

    assert config.dispatch.timeouts.default_minutes == 30
    assert config.dispatch.audit.enabled is True
    assert config.dispatch.audit.max_bytes == 262_144

    assert config.review_valve.every_n_work_units == 5
    assert config.review_valve.end_of_epic is True
    assert config.fix_rounds.max_rounds_per_blocker == 2
    assert config.readiness.escalation_threshold == 3

    assert [gate.name for gate in config.gates] == ["lint", "test"]
    assert config.gates[0].command == "just lint"
    assert config.gates[0].blocking is True
    assert config.gates[0].mode == "strict"

    assert config.prerequisites.infra["git"] == "any"
    assert config.prerequisites.lsp_languages == ("python",)

    assert config.tracker.kind == "github"
    assert config.tracker.repo == "krazyuniks/woof"

    assert config.test_markers.languages["python"].comment_prefix == "#"
    assert config.docs_paths.mappings == ()


def test_resolved_config_is_frozen(woof_home: Path) -> None:
    write_project_config("demo", MINIMAL_PROJECT_CONFIG)
    config = load_project_config("demo")
    with pytest.raises(AttributeError):
        config.delivery.profile = "A"  # type: ignore[misc]


def test_malformed_toml_is_a_hard_error(woof_home: Path) -> None:
    write_project_config("broken", "this is not = = toml")
    with pytest.raises(ProjectConfigError) as excinfo:
        load_project_config("broken")
    assert "broken.toml" in str(excinfo.value)


def test_an_undeclared_default_run_profile_is_a_hard_error(woof_home: Path) -> None:
    write_project_config(
        "demo",
        MINIMAL_PROJECT_CONFIG.replace(
            'default_run_profile = "default"', 'default_run_profile = "absent"'
        ),
    )
    with pytest.raises(ProjectConfigError) as excinfo:
        load_project_config("demo")
    assert "absent" in str(excinfo.value)


def test_docs_paths_mappings_are_resolved_when_declared(woof_home: Path) -> None:
    write_project_config(
        "demo",
        MINIMAL_PROJECT_CONFIG
        + """
[[docs_paths.mappings]]
code_pattern = "src/**/*.py"
doc_pattern = "docs/**/*.md"
rationale = "engine behaviour is documented"
""",
    )
    config = load_project_config("demo")
    assert len(config.docs_paths.mappings) == 1
    assert config.docs_paths.mappings[0].code_pattern == "src/**/*.py"
    assert config.docs_paths.mappings[0].doc_pattern == "docs/**/*.md"


def test_the_project_key_is_never_derived_from_a_directory_name(
    woof_home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two checkouts named `main` under different containers must not collide."""

    monkeypatch.delenv("WOOF_PROJECT", raising=False)
    checkout = tmp_path / "some-container" / "main"
    checkout.mkdir(parents=True)
    monkeypatch.chdir(checkout)

    with pytest.raises(Exception) as excinfo:
        load_project_config()
    assert "--project" in str(excinfo.value)


def test_raw_config_round_trips_the_declaration_for_validation(woof_home: Path) -> None:
    write_project_config("demo", MINIMAL_PROJECT_CONFIG)
    raw = load_raw_project_config("demo")
    assert raw["schema_version"] == 1
    assert raw["type"] == "woof_project"
    assert raw["delivery"]["profile"] == "B"

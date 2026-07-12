"""Shared test scaffolding for the operator-home project config (ADR-017).

Every test runs against a throwaway ``WOOF_HOME`` (see the autouse fixture in
``tests/conftest.py``). Tests that need project config write it here rather than
building an in-repo ``.woof/`` directory by hand.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from woof.paths import project_config_path

DEFAULT_PROJECT_KEY = "test-project"

MINIMAL_PROJECT_CONFIG = """\
schema_version = 1
type = "woof_project"
default_run_profile = "default"

[delivery]
profile = "B"
repo_root = "."
toolchain_root = "."
base_branch = "main"

[profiles.B]
commit = true
push = false

[verification]
command = "just check"
timeout_seconds = 600

[run_profiles.default.producer]
harness = "codex"
model = "gpt-5.5"
effort = "high"

[run_profiles.default.reviewer]
harness = "claude"
model = "opus"
effort = "xhigh"

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

[checks.review_size]
max_non_generated_changed_lines = 500

[cartography]
floor = "structural"
staleness_floor_hours = 168
summary_min_chars = 200
stub_marker = "<!-- woof:stub -->"
languages = ["python"]

[drain]
merge_after_ready_pr = false
rerun_after_merge = true
mark_unit_done_after_publish = true
commit_backlog_state = true
stop_when_no_eligible_units = true

[dispatch.timeouts]
default_minutes = 30

[dispatch.audit]
enabled = true
max_bytes = 262144
redact_patterns = []

[review_valve]
every_n_work_units = 5
end_of_epic = true

[fix_rounds]
max_rounds_per_blocker = 2

[readiness]
escalation_threshold = 3

[gates.lint]
command = "just lint"
timeout_seconds = 120

[gates.test]
command = "just test"
timeout_seconds = 360

[prerequisites.infra]
just = "any"
git = "any"
gh = "any"

[prerequisites.commands]
claude = "any"
codex = "any"

[prerequisites.validators]
ajv = "any"
ajv-formats = "any"

[prerequisites.lsp]
languages = ["python"]

[tracker]
kind = "github"
repo = "krazyuniks/woof"

[test_markers.languages.python]
test_paths = ["tests/", "src/**/test_*.py"]
marker_regex = '(?<![A-Za-z0-9])O\\d+(?![A-Za-z0-9])'
cd_marker_regex = '(?<![A-Za-z0-9])CD\\d+(?![A-Za-z0-9])'
docstring_keyword = "outcomes:"
comment_prefix = "#"
context_lines = 3
"""


def write_project_config(key: str, body: str) -> Path:
    """Write ``body`` as the project config for ``key`` in the active WOOF_HOME."""

    path = project_config_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def use_project(key: str = DEFAULT_PROJECT_KEY) -> str:
    """Point the process-wide project key at ``key`` and return it."""

    os.environ["WOOF_PROJECT"] = key
    return key


def seed_project_config(overrides: dict[str, Any] | None = None, key: str | None = None) -> Path:
    """Write the default project config with ``overrides`` deep-merged in.

    This is the one place a test declares project config. ``overrides`` is a
    nested mapping matching the config's own shape, so a test that cares about
    one gate writes only that gate:

        seed_project_config({"gates": {"test": {"command": "false"}}})

    A section set to None is removed, which is how a test asserts the behaviour
    of an undeclared section.
    """

    project_key = key or os.environ.get("WOOF_PROJECT") or DEFAULT_PROJECT_KEY
    data = tomllib.loads(MINIMAL_PROJECT_CONFIG)
    _deep_merge(data, overrides or {})
    return write_project_config(project_key, render_toml(data))


def _deep_merge(target: dict[str, Any], overrides: dict[str, Any]) -> None:
    for key, value in overrides.items():
        if value is None:
            target.pop(key, None)
        elif isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = value


def render_toml(data: dict[str, Any], prefix: str = "") -> str:
    """Render a nested mapping as TOML.

    The standard library reads TOML but does not write it, and the test suite
    needs to write project configs from structured overrides rather than by
    string-splicing sections together.
    """

    scalars: list[str] = []
    tables: list[str] = []
    for key, value in data.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            body = render_toml(value, prefix=f"{path}.")
            tables.append(f"[{path}]\n{body}" if body.strip() else f"[{path}]\n")
        elif isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            for item in value:
                tables.append(f"[[{path}]]\n{render_toml(item, prefix=f'{path}.')}")
        else:
            scalars.append(f"{key} = {_toml_value(value)}")
    head = "\n".join(scalars)
    if head:
        head += "\n"
    return head + "\n".join(tables)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    text = str(value)
    if "'" not in text and ("\\" in text or '"' in text):
        return f"'{text}'"
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'

"""Every harness profile declares its transport backend explicitly.

Project policy selects a harness, an optional model, and an optional effort. It
never selects a transport: the backend is a property of the profile, so no
caller has to know which backend a harness runs on.
"""

from __future__ import annotations

import pytest

from woof.cli.harness_registry import (
    BACKEND_HERDR,
    BACKEND_TMUX,
    BACKENDS,
    HARNESS_ALIASES,
    HARNESS_PROFILES,
    HarnessError,
    get_profile,
    harness_backend,
    resolve_harness_config,
)


def test_every_profile_declares_a_known_backend() -> None:
    for name, profile in HARNESS_PROFILES.items():
        assert profile.backend in BACKENDS, f"{name} declares backend {profile.backend!r}"


def test_claude_code_family_and_codex_route_to_herdr() -> None:
    for harness in ("claude", "codex", "deepseek", "glm"):
        assert get_profile(harness).backend == BACKEND_HERDR


def test_profiles_without_a_lifecycle_integration_stay_on_tmux() -> None:
    for harness in ("reasonix", "pi"):
        assert get_profile(harness).backend == BACKEND_TMUX


def test_alias_resolves_to_the_canonical_profile_backend() -> None:
    for alias, canonical in HARNESS_ALIASES.items():
        assert get_profile(alias).backend == HARNESS_PROFILES[canonical].backend


def test_resolved_config_carries_the_backend() -> None:
    resolved = resolve_harness_config("claude", model="sonnet", effort="high")
    assert resolved.backend == BACKEND_HERDR
    assert resolved.harness == "claude"


def test_harness_backend_rejects_an_unknown_harness() -> None:
    with pytest.raises(HarnessError):
        harness_backend("nonexistent-harness")

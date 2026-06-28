"""Interactive TUI harness registry for Woof dispatch.

Woof owns the harness/model/effort catalogue for graph workers. The
``tmux_harness`` package remains pure transport: it launches a prebuilt argv,
delivers a prompt through a file, captures the structured answer, and tears down
the tmux session.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class HarnessError(Exception):
    """Raised for unknown or misconfigured harness identifiers."""


@dataclass(frozen=True)
class HarnessProfile:
    name: str
    base: list[str]
    model_flag: tuple[str, str] | None
    effort_flag: tuple[str, str] | None
    trailer: list[str]
    default_model: str
    default_effort: str
    effort_levels: tuple[str, ...] = field(default_factory=tuple)

    @property
    def supports_effort(self) -> bool:
        return self.effort_flag is not None


HARNESS_PROFILES: dict[str, HarnessProfile] = {
    "claude": HarnessProfile(
        name="claude",
        base=["cld"],
        model_flag=("--model", "{model}"),
        effort_flag=("--effort", "{effort}"),
        trailer=["--dangerously-skip-permissions"],
        default_model="sonnet",
        default_effort="high",
        effort_levels=("low", "medium", "high", "xhigh", "max"),
    ),
    "codex": HarnessProfile(
        name="codex",
        base=["codex", "-s", "danger-full-access", "-a", "never"],
        model_flag=("-m", "{model}"),
        effort_flag=("-c", "model_reasoning_effort={effort}"),
        trailer=[],
        default_model="gpt-5.5",
        default_effort="medium",
        effort_levels=("low", "medium", "high", "xhigh"),
    ),
    "deepseek": HarnessProfile(
        name="deepseek",
        base=["deepclaude", "-b", "ds", "--"],
        model_flag=("--model", "{model}"),
        effort_flag=("--effort", "{effort}"),
        trailer=["--dangerously-skip-permissions"],
        default_model="opus",
        default_effort="max",
        effort_levels=("low", "medium", "high", "max"),
    ),
    "reasonix": HarnessProfile(
        name="reasonix",
        base=["reasonix", "code", "--no-dashboard"],
        model_flag=("--model", "{model}"),
        effort_flag=("--effort", "{effort}"),
        trailer=[],
        default_model="",
        default_effort="max",
        effort_levels=("low", "medium", "high", "max"),
    ),
    "pi": HarnessProfile(
        name="pi",
        base=["pi"],
        model_flag=("--model", "{model}"),
        effort_flag=None,
        trailer=[],
        default_model="",
        default_effort="",
    ),
    "glm": HarnessProfile(
        name="glm",
        base=["zsh", "-ic", 'glm "$@"', "glm"],
        model_flag=("--model", "{model}"),
        effort_flag=("--effort", "{effort}"),
        trailer=["--dangerously-skip-permissions"],
        default_model="opus",
        default_effort="max",
        effort_levels=("high", "max"),
    ),
}

HARNESS_ALIASES = {
    "cld": "claude",
    "cod": "codex",
    "deepclaude": "deepseek",
}


def canonical_harness(harness: str) -> str:
    return HARNESS_ALIASES.get(harness, harness)


def get_profile(harness: str) -> HarnessProfile:
    profile = HARNESS_PROFILES.get(canonical_harness(harness))
    if profile is None:
        known = ", ".join(sorted(HARNESS_PROFILES) + sorted(HARNESS_ALIASES))
        raise HarnessError(f"Unknown harness: {harness!r}. Known: {known}")
    return profile


def supported_harnesses() -> tuple[str, ...]:
    return tuple(sorted(HARNESS_PROFILES))


def build_launch_argv(
    harness: str,
    *,
    model: str | None = None,
    effort: str | None = None,
) -> list[str]:
    profile = get_profile(harness)
    resolved_model = profile.default_model if model is None else model
    resolved_effort = profile.default_effort if effort is None else effort
    if (
        profile.supports_effort
        and resolved_effort
        and profile.effort_levels
        and resolved_effort not in profile.effort_levels
    ):
        resolved_effort = profile.default_effort

    argv = list(profile.base)
    if profile.model_flag is not None and resolved_model:
        flag, value = profile.model_flag
        argv.extend([flag, value.format(model=resolved_model)])
    if profile.effort_flag is not None and resolved_effort:
        flag, value = profile.effort_flag
        argv.extend([flag, value.format(effort=resolved_effort)])
    argv.extend(profile.trailer)
    return argv

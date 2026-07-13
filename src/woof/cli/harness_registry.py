"""Interactive TUI harness registry for Woof dispatch.

Woof owns the harness/model/effort catalogue for graph workers, and each profile
declares the transport backend its TUI runs on. The transport packages stay pure
mechanics: they launch a prebuilt argv, deliver a prompt through a file, capture
the structured answer, and tear the worker down.

The backend is a property of the profile, never a project-policy choice and never
a branch in workflow code. Project policy selects a harness, an optional model,
and an optional effort; the backend follows from the harness.
"""

from __future__ import annotations

from dataclasses import dataclass, field

BACKEND_TMUX = "tmux"
BACKEND_HERDR = "herdr"
BACKENDS = (BACKEND_TMUX, BACKEND_HERDR)


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
    backend: str
    effort_levels: tuple[str, ...] = field(default_factory=tuple)

    @property
    def supports_effort(self) -> bool:
        return self.effort_flag is not None


@dataclass(frozen=True)
class ResolvedHarnessConfig:
    profile: HarnessProfile
    harness: str
    model: str
    effort: str

    @property
    def backend(self) -> str:
        return self.profile.backend


# The Claude Code family (claude, deepseek, glm -- all the Claude Code TUI) and
# codex have validated herdr lifecycle integrations, so herdr reports their
# working/idle/blocked/done transitions over the socket. reasonix and pi have no
# such integration and stay on tmux, where readiness and completion are observed
# from the terminal instead.
HARNESS_PROFILES: dict[str, HarnessProfile] = {
    "claude": HarnessProfile(
        name="claude",
        base=["cld"],
        model_flag=("--model", "{model}"),
        effort_flag=("--effort", "{effort}"),
        trailer=["--dangerously-skip-permissions"],
        default_model="sonnet",
        default_effort="high",
        backend=BACKEND_HERDR,
        effort_levels=("low", "medium", "high", "xhigh", "max"),
    ),
    "codex": HarnessProfile(
        name="codex",
        base=["codex", "-s", "danger-full-access", "-a", "never"],
        model_flag=("-m", "{model}"),
        effort_flag=("-c", "model_reasoning_effort={effort}"),
        trailer=[],
        default_model="gpt-5.6-sol",
        default_effort="high",
        backend=BACKEND_HERDR,
        effort_levels=("none", "low", "medium", "high", "xhigh", "max"),
    ),
    # DeepSeek driven through the Claude Code TUI via the deepclaude proxy.
    "deepseek": HarnessProfile(
        name="deepseek",
        base=["deepclaude", "-b", "ds", "--"],
        model_flag=("--model", "{model}"),
        effort_flag=("--effort", "{effort}"),
        trailer=["--dangerously-skip-permissions"],
        default_model="opus",
        default_effort="max",
        backend=BACKEND_HERDR,
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
        backend=BACKEND_TMUX,
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
        backend=BACKEND_TMUX,
    ),
    # GLM (Z.ai) driven through the Claude Code TUI. ``glm`` is a shell function,
    # which does not resolve under a non-interactive shell, so the launch wraps an
    # interactive zsh and the appended flags reach the function through "$@".
    "glm": HarnessProfile(
        name="glm",
        base=["zsh", "-ic", 'glm "$@"', "glm"],
        model_flag=("--model", "{model}"),
        effort_flag=("--effort", "{effort}"),
        trailer=["--dangerously-skip-permissions"],
        default_model="opus",
        default_effort="max",
        backend=BACKEND_HERDR,
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


def harness_backend(harness: str) -> str:
    """Return the transport backend declared by this harness profile."""
    return get_profile(harness).backend


def resolve_harness_config(
    harness: str,
    *,
    model: str | None = None,
    effort: str | None = None,
) -> ResolvedHarnessConfig:
    profile = get_profile(harness)
    resolved_model = profile.default_model if model is None else model
    resolved_effort = profile.default_effort if effort is None else effort
    if not profile.supports_effort and resolved_effort:
        raise HarnessError(f"{profile.name} does not support effort selection")
    if profile.effort_levels and resolved_effort and resolved_effort not in profile.effort_levels:
        raise HarnessError(
            f"{profile.name} effort {resolved_effort!r} is not supported; "
            f"expected one of {sorted(profile.effort_levels)}"
        )
    return ResolvedHarnessConfig(
        profile=profile,
        harness=profile.name,
        model=resolved_model,
        effort=resolved_effort,
    )


def build_launch_argv(
    harness: str,
    *,
    model: str | None = None,
    effort: str | None = None,
) -> list[str]:
    resolved = resolve_harness_config(harness, model=model, effort=effort)
    profile = resolved.profile

    argv = list(profile.base)
    if profile.model_flag is not None and resolved.model:
        flag, value = profile.model_flag
        argv.extend([flag, value.format(model=resolved.model)])
    if profile.effort_flag is not None and resolved.effort:
        flag, value = profile.effort_flag
        argv.extend([flag, value.format(effort=resolved.effort)])
    argv.extend(profile.trailer)
    return argv

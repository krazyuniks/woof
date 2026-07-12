"""The single per-project Woof config, read from the operator home (ADR-017).

One project, one config file: ``~/.woof/config/projects/<project-key>.toml``.
It carries the sections that were once split across six files in the driven
repo, so a delivery repository carries no trace of the engine that builds it.

There is exactly one source, so there is nothing to disambiguate: a missing
file is a hard error, and no in-repo location is consulted, deprecated or
otherwise. Consumers receive resolved frozen dataclasses; the raw mapping is
exposed only to the validator that reports on the declaration itself.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from woof.paths import project_config_path, resolve_project_key

CONFIG_TYPE = "woof_project"
CONFIG_SCHEMA_VERSION = 1

DELIVERY_PROFILES = ("A", "B")
CARTOGRAPHY_FLOORS = ("none", "design", "lexical", "structural")
CHECK_FLOOR_IDS = (
    "quality-gates",
    "outcome-markers",
    "scope",
    "contract-refs",
    "plan-crossrefs",
    "critique-blocker",
    "commit-transaction",
    "docs-drift",
    "review-valve",
)
RUN_PROFILE_ROLES = ("producer", "reviewer")
TRACKER_KINDS = ("github", "local")

DEFAULT_TIMEOUT_MINUTES = 30
DEFAULT_IDLE_SECONDS = 600.0
DEFAULT_COMPLETION_GRACE_SECONDS = 60.0
DEFAULT_COMPLETION_TAIL_CAP_SECONDS = 120.0
DEFAULT_AUDIT_MAX_BYTES = 262_144
DEFAULT_GATE_TIMEOUT_SECONDS = 300
DEFAULT_REVIEW_EVERY_N_WORK_UNITS = 5
DEFAULT_REVIEW_END_OF_EPIC = True
DEFAULT_FIX_ROUNDS_PER_BLOCKER = 2
DEFAULT_READINESS_ESCALATION_THRESHOLD = 3
DEFAULT_REVIEW_SIZE_MAX_LINES = 500
DEFAULT_STALENESS_FLOOR_HOURS = 168
DEFAULT_SUMMARY_MIN_CHARS = 200
DEFAULT_STUB_MARKER = "<!-- woof:stub -->"
DEFAULT_VERIFICATION_TIMEOUT_SECONDS = 600

GATE_MODES = ("strict", "baseline")


class ProjectConfigError(Exception):
    """Raised when a project's Woof config is missing or malformed."""


@dataclass(frozen=True)
class DeliveryConfig:
    profile: str
    repo_root: str
    toolchain_root: str
    base_branch: str


@dataclass(frozen=True)
class WorktreeConfig:
    root: str
    derivation: str = "unit_id"


@dataclass(frozen=True)
class ProfileAConfig:
    github_repo: str
    ready_label: str
    merge_path_groups: tuple[str, ...] = ()
    terminal_deploy_checks: tuple[str, ...] = ()
    mergeability_settle_timeout: int | None = None
    deploy_wait_timeout: int | None = None
    merge_attempts: int | None = None
    merge_interval_s: float | None = None
    worktree: WorktreeConfig | None = None


@dataclass(frozen=True)
class ProfileBConfig:
    commit: bool
    push: bool


@dataclass(frozen=True)
class VerificationConfig:
    command: str
    timeout_seconds: int = DEFAULT_VERIFICATION_TIMEOUT_SECONDS


@dataclass(frozen=True)
class RunProfileSlot:
    harness: str
    model: str | None = None
    effort: str | None = None


@dataclass(frozen=True)
class RunProfile:
    name: str
    producer: RunProfileSlot
    reviewer: RunProfileSlot


@dataclass(frozen=True)
class ReviewSizeConfig:
    max_non_generated_changed_lines: int = DEFAULT_REVIEW_SIZE_MAX_LINES


@dataclass(frozen=True)
class ChecksConfig:
    """The deterministic check floor, plus the opt-in review-size guard.

    ``review_size`` is None when the project does not declare one: the guard
    stays off rather than acquiring a threshold nobody asked for.
    """

    floor: tuple[str, ...]
    review_size: ReviewSizeConfig | None = None


@dataclass(frozen=True)
class CartographyConfig:
    """Cartography floor plus the details the non-``none`` floors need.

    The floor and its supporting details were split across two files; they are
    one concept and now have one home.
    """

    floor: str = "structural"
    staleness_floor_hours: int = DEFAULT_STALENESS_FLOOR_HOURS
    summary_min_chars: int = DEFAULT_SUMMARY_MIN_CHARS
    stub_marker: str = DEFAULT_STUB_MARKER
    languages: tuple[str, ...] = ()
    declared: bool = False


@dataclass(frozen=True)
class DrainConfig:
    merge_after_ready_pr: bool = False
    rerun_after_merge: bool = True
    mark_unit_done_after_publish: bool = True
    commit_backlog_state: bool = True
    stop_when_no_eligible_units: bool = True


@dataclass(frozen=True)
class TimeoutsConfig:
    default_minutes: float = DEFAULT_TIMEOUT_MINUTES
    idle_seconds: float = DEFAULT_IDLE_SECONDS
    completion_grace_seconds: float = DEFAULT_COMPLETION_GRACE_SECONDS
    completion_tail_cap_seconds: float = DEFAULT_COMPLETION_TAIL_CAP_SECONDS


@dataclass(frozen=True)
class AuditConfig:
    enabled: bool = True
    max_bytes: int = DEFAULT_AUDIT_MAX_BYTES
    redact_patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class DispatchConfig:
    timeouts: TimeoutsConfig = field(default_factory=TimeoutsConfig)
    audit: AuditConfig = field(default_factory=AuditConfig)


@dataclass(frozen=True)
class ReviewValveConfig:
    every_n_work_units: int = DEFAULT_REVIEW_EVERY_N_WORK_UNITS
    end_of_epic: bool = DEFAULT_REVIEW_END_OF_EPIC


@dataclass(frozen=True)
class FixRoundsConfig:
    max_rounds_per_blocker: int = DEFAULT_FIX_ROUNDS_PER_BLOCKER


@dataclass(frozen=True)
class ReadinessConfig:
    escalation_threshold: int = DEFAULT_READINESS_ESCALATION_THRESHOLD


@dataclass(frozen=True)
class GateSpec:
    name: str
    command: str
    timeout_seconds: int = DEFAULT_GATE_TIMEOUT_SECONDS
    blocking: bool = True
    mode: str = "strict"


@dataclass(frozen=True)
class PrerequisitesConfig:
    infra: dict[str, str] = field(default_factory=dict)
    commands: dict[str, str] = field(default_factory=dict)
    validators: dict[str, str] = field(default_factory=dict)
    host: dict[str, Any] = field(default_factory=dict)
    servers: dict[str, Any] = field(default_factory=dict)
    indexing: dict[str, Any] = field(default_factory=dict)
    lsp_languages: tuple[str, ...] = ()


@dataclass(frozen=True)
class TrackerConfig:
    kind: str
    repo: str | None = None


@dataclass(frozen=True)
class TestMarkerLanguage:
    name: str
    test_paths: tuple[str, ...]
    marker_regex: str
    cd_marker_regex: str
    docstring_keyword: str
    comment_prefix: str
    context_lines: int = 3


@dataclass(frozen=True)
class TestMarkersConfig:
    languages: dict[str, TestMarkerLanguage] = field(default_factory=dict)
    declared: bool = False


@dataclass(frozen=True)
class DocsPathMapping:
    code_pattern: str
    doc_pattern: str
    rationale: str | None = None


@dataclass(frozen=True)
class DocsPathsConfig:
    mappings: tuple[DocsPathMapping, ...] = ()
    declared: bool = False


@dataclass(frozen=True)
class ProjectConfig:
    """Everything the engine needs to drive one project."""

    key: str
    source: Path
    delivery: DeliveryConfig
    verification: VerificationConfig
    run_profile: RunProfile
    run_profiles: dict[str, RunProfile]
    checks: ChecksConfig
    cartography: CartographyConfig
    drain: DrainConfig
    dispatch: DispatchConfig
    review_valve: ReviewValveConfig
    fix_rounds: FixRoundsConfig
    readiness: ReadinessConfig
    gates: tuple[GateSpec, ...]
    prerequisites: PrerequisitesConfig
    tracker: TrackerConfig
    test_markers: TestMarkersConfig
    docs_paths: DocsPathsConfig
    profile_a: ProfileAConfig | None = None
    profile_b: ProfileBConfig | None = None

    def gate(self, name: str) -> GateSpec | None:
        for spec in self.gates:
            if spec.name == name:
                return spec
        return None

    @property
    def gate_names(self) -> set[str]:
        return {spec.name for spec in self.gates}


# Parsed configs are cached on (path, mtime_ns, size) so a single CLI
# invocation parses the file once, and an edited file always re-parses.
_CACHE: dict[tuple[str, int, int], ProjectConfig] = {}


def load_raw_project_config(project_key: str | None = None) -> dict[str, Any]:
    """Return the parsed TOML declaration for the project, unresolved.

    Only the preflight validator uses this: its job is to report on the
    declaration itself, so it needs the shape as written.
    """

    key = resolve_project_key(project_key)
    return _read_toml(_require_config_path(key))


def load_project_config(project_key: str | None = None) -> ProjectConfig:
    """Load and resolve the project's config from the operator home."""

    key = resolve_project_key(project_key)
    path = _require_config_path(key)
    try:
        stat = path.stat()
        cache_key = (str(path), stat.st_mtime_ns, stat.st_size)
    except OSError as exc:
        raise ProjectConfigError(f"cannot read project config: {path}: {exc}") from exc

    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    config = _resolve(key, path, _read_toml(path))
    _CACHE[cache_key] = config
    return config


def _require_config_path(project_key: str) -> Path:
    path = project_config_path(project_key)
    if not path.is_file():
        raise ProjectConfigError(
            f"missing Woof project config: {path}\n"
            f"Engine config is never read from the driven repo. Create it with:\n"
            f"  woof init --project {project_key}"
        )
    return path


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProjectConfigError(f"cannot read project config: {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ProjectConfigError(f"{path} is not valid TOML: {exc}") from exc


def _resolve(key: str, path: Path, raw: dict[str, Any]) -> ProjectConfig:
    _check_header(raw, path)
    delivery = _delivery(raw, path)
    run_profiles = _run_profiles(raw, path)
    default_name = _required_str(raw, "default_run_profile", path)
    if default_name not in run_profiles:
        raise ProjectConfigError(f"{path} default_run_profile {default_name!r} is not declared")

    return ProjectConfig(
        key=key,
        source=path,
        delivery=delivery,
        profile_a=_profile_a(raw, path),
        profile_b=_profile_b(raw, path),
        verification=_verification(raw, path),
        run_profile=run_profiles[default_name],
        run_profiles=run_profiles,
        checks=_checks(raw, path),
        cartography=_cartography(raw, path),
        drain=_drain(raw, path),
        dispatch=_dispatch(raw, path),
        review_valve=_review_valve(raw, path),
        fix_rounds=_fix_rounds(raw, path),
        readiness=_readiness(raw, path),
        gates=_gates(raw, path),
        prerequisites=_prerequisites(raw, path),
        tracker=_tracker(raw, path),
        test_markers=_test_markers(raw, path),
        docs_paths=_docs_paths(raw, path),
    )


def _check_header(raw: dict[str, Any], path: Path) -> None:
    version = raw.get("schema_version")
    if version is not None and version != CONFIG_SCHEMA_VERSION:
        raise ProjectConfigError(f"{path} schema_version must be {CONFIG_SCHEMA_VERSION}")
    config_type = raw.get("type")
    if config_type is not None and config_type != CONFIG_TYPE:
        raise ProjectConfigError(f"{path} type must be {CONFIG_TYPE!r}")


def _table(raw: dict[str, Any], name: str, path: Path) -> dict[str, Any]:
    value = raw.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ProjectConfigError(f"{path} [{name}] must be a table")
    return value


def _required_str(mapping: dict[str, Any], key: str, path: Path, *, prefix: str = "") -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProjectConfigError(f"{path} {prefix}{key} must be a non-empty string")
    return value


def _optional_str(mapping: dict[str, Any], key: str, path: Path, *, prefix: str = "") -> str | None:
    value = mapping.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ProjectConfigError(f"{path} {prefix}{key} must be a non-empty string")
    return value


def _positive_int(
    mapping: dict[str, Any], key: str, path: Path, default: int, *, prefix: str = ""
) -> int:
    value = mapping.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ProjectConfigError(f"{path} {prefix}{key} must be a positive integer")
    return value


def _non_negative_int(
    mapping: dict[str, Any], key: str, path: Path, default: int, *, prefix: str = ""
) -> int:
    value = mapping.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ProjectConfigError(f"{path} {prefix}{key} must be a non-negative integer")
    return value


def _bool(
    mapping: dict[str, Any], key: str, path: Path, default: bool, *, prefix: str = ""
) -> bool:
    value = mapping.get(key, default)
    if not isinstance(value, bool):
        raise ProjectConfigError(f"{path} {prefix}{key} must be a boolean")
    return value


def _str_tuple(
    mapping: dict[str, Any], key: str, path: Path, *, prefix: str = ""
) -> tuple[str, ...]:
    value = mapping.get(key)
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ProjectConfigError(f"{path} {prefix}{key} must be an array of strings")
    return tuple(value)


def _delivery(raw: dict[str, Any], path: Path) -> DeliveryConfig:
    delivery = _table(raw, "delivery", path)
    profile = delivery.get("profile")
    if profile not in DELIVERY_PROFILES:
        raise ProjectConfigError(f"{path} delivery.profile must be one of {DELIVERY_PROFILES}")
    return DeliveryConfig(
        profile=profile,
        repo_root=_required_str(delivery, "repo_root", path, prefix="delivery."),
        toolchain_root=_required_str(delivery, "toolchain_root", path, prefix="delivery."),
        base_branch=_required_str(delivery, "base_branch", path, prefix="delivery."),
    )


def _profiles(raw: dict[str, Any], path: Path) -> dict[str, Any]:
    return _table(raw, "profiles", path)


def _profile_a(raw: dict[str, Any], path: Path) -> ProfileAConfig | None:
    block = _profiles(raw, path).get("A")
    if not isinstance(block, dict):
        return None
    worktree_raw = block.get("worktree")
    worktree: WorktreeConfig | None = None
    if isinstance(worktree_raw, dict):
        derivation = worktree_raw.get("derivation", "unit_id")
        if derivation not in {"unit_id", "manifest_map"}:
            raise ProjectConfigError(
                f"{path} profiles.A.worktree.derivation must be unit_id or manifest_map"
            )
        worktree = WorktreeConfig(
            root=_required_str(worktree_raw, "root", path, prefix="profiles.A.worktree."),
            derivation=derivation,
        )
    merge_interval = block.get("merge_interval_s")
    if merge_interval is not None and (
        not isinstance(merge_interval, int | float) or isinstance(merge_interval, bool)
    ):
        raise ProjectConfigError(f"{path} profiles.A.merge_interval_s must be a number")
    return ProfileAConfig(
        github_repo=_required_str(block, "github_repo", path, prefix="profiles.A."),
        ready_label=_required_str(block, "ready_label", path, prefix="profiles.A."),
        merge_path_groups=_str_tuple(block, "merge_path_groups", path, prefix="profiles.A."),
        terminal_deploy_checks=_str_tuple(
            block, "terminal_deploy_checks", path, prefix="profiles.A."
        ),
        mergeability_settle_timeout=block.get("mergeability_settle_timeout"),
        deploy_wait_timeout=block.get("deploy_wait_timeout"),
        merge_attempts=block.get("merge_attempts"),
        merge_interval_s=float(merge_interval) if merge_interval is not None else None,
        worktree=worktree,
    )


def _profile_b(raw: dict[str, Any], path: Path) -> ProfileBConfig | None:
    block = _profiles(raw, path).get("B")
    if not isinstance(block, dict):
        return None
    return ProfileBConfig(
        commit=_bool(block, "commit", path, True, prefix="profiles.B."),
        push=_bool(block, "push", path, False, prefix="profiles.B."),
    )


def _verification(raw: dict[str, Any], path: Path) -> VerificationConfig:
    block = _table(raw, "verification", path)
    return VerificationConfig(
        command=_required_str(block, "command", path, prefix="verification."),
        timeout_seconds=_positive_int(
            block,
            "timeout_seconds",
            path,
            DEFAULT_VERIFICATION_TIMEOUT_SECONDS,
            prefix="verification.",
        ),
    )


def _run_profiles(raw: dict[str, Any], path: Path) -> dict[str, RunProfile]:
    block = _table(raw, "run_profiles", path)
    if not block:
        raise ProjectConfigError(f"{path} [run_profiles] must declare at least one profile")
    profiles: dict[str, RunProfile] = {}
    for name, body in block.items():
        if not isinstance(body, dict):
            raise ProjectConfigError(f"{path} run_profiles.{name} must be a table")
        slots: dict[str, RunProfileSlot] = {}
        for role in RUN_PROFILE_ROLES:
            slot = body.get(role)
            if not isinstance(slot, dict):
                raise ProjectConfigError(f"{path} run_profiles.{name}.{role} must be a table")
            prefix = f"run_profiles.{name}.{role}."
            slots[role] = RunProfileSlot(
                harness=_required_str(slot, "harness", path, prefix=prefix),
                model=_optional_str(slot, "model", path, prefix=prefix),
                effort=_optional_str(slot, "effort", path, prefix=prefix),
            )
        profiles[name] = RunProfile(
            name=name, producer=slots["producer"], reviewer=slots["reviewer"]
        )
    return profiles


def _checks(raw: dict[str, Any], path: Path) -> ChecksConfig:
    block = _table(raw, "checks", path)
    floor = _str_tuple(block, "floor", path, prefix="checks.")
    if not floor:
        raise ProjectConfigError(f"{path} checks.floor must list at least one check")
    unknown = sorted(set(floor) - set(CHECK_FLOOR_IDS))
    if unknown:
        raise ProjectConfigError(f"{path} unknown checks.floor value(s): {', '.join(unknown)}")
    review_size_raw = block.get("review_size")
    review_size: ReviewSizeConfig | None
    if review_size_raw is None:
        review_size = None
    elif isinstance(review_size_raw, dict):
        review_size = ReviewSizeConfig(
            max_non_generated_changed_lines=_positive_int(
                review_size_raw,
                "max_non_generated_changed_lines",
                path,
                DEFAULT_REVIEW_SIZE_MAX_LINES,
                prefix="checks.review_size.",
            )
        )
    else:
        raise ProjectConfigError(f"{path} [checks.review_size] must be a table")
    return ChecksConfig(floor=floor, review_size=review_size)


def _cartography(raw: dict[str, Any], path: Path) -> CartographyConfig:
    block = _table(raw, "cartography", path)
    floor = block.get("floor", "structural")
    if floor not in CARTOGRAPHY_FLOORS:
        raise ProjectConfigError(
            f"{path} cartography.floor must be one of {', '.join(CARTOGRAPHY_FLOORS)}"
        )
    return CartographyConfig(
        floor=floor,
        staleness_floor_hours=_positive_int(
            block,
            "staleness_floor_hours",
            path,
            DEFAULT_STALENESS_FLOOR_HOURS,
            prefix="cartography.",
        ),
        summary_min_chars=_positive_int(
            block, "summary_min_chars", path, DEFAULT_SUMMARY_MIN_CHARS, prefix="cartography."
        ),
        stub_marker=block.get("stub_marker") or DEFAULT_STUB_MARKER,
        languages=_str_tuple(block, "languages", path, prefix="cartography."),
        declared=bool(block),
    )


def _drain(raw: dict[str, Any], path: Path) -> DrainConfig:
    block = _table(raw, "drain", path)
    defaults = DrainConfig()
    return DrainConfig(
        merge_after_ready_pr=_bool(
            block, "merge_after_ready_pr", path, defaults.merge_after_ready_pr, prefix="drain."
        ),
        rerun_after_merge=_bool(
            block, "rerun_after_merge", path, defaults.rerun_after_merge, prefix="drain."
        ),
        mark_unit_done_after_publish=_bool(
            block,
            "mark_unit_done_after_publish",
            path,
            defaults.mark_unit_done_after_publish,
            prefix="drain.",
        ),
        commit_backlog_state=_bool(
            block, "commit_backlog_state", path, defaults.commit_backlog_state, prefix="drain."
        ),
        stop_when_no_eligible_units=_bool(
            block,
            "stop_when_no_eligible_units",
            path,
            defaults.stop_when_no_eligible_units,
            prefix="drain.",
        ),
    )


def _dispatch(raw: dict[str, Any], path: Path) -> DispatchConfig:
    block = _table(raw, "dispatch", path)
    timeouts_raw = block.get("timeouts")
    if timeouts_raw is None:
        timeouts = TimeoutsConfig()
    elif isinstance(timeouts_raw, dict):
        timeouts = TimeoutsConfig(
            default_minutes=_positive_number(
                timeouts_raw, "default_minutes", path, DEFAULT_TIMEOUT_MINUTES
            ),
            idle_seconds=_non_negative_number(
                timeouts_raw, "idle_seconds", path, DEFAULT_IDLE_SECONDS
            ),
            completion_grace_seconds=_non_negative_number(
                timeouts_raw,
                "completion_grace_seconds",
                path,
                DEFAULT_COMPLETION_GRACE_SECONDS,
            ),
            completion_tail_cap_seconds=_non_negative_number(
                timeouts_raw,
                "completion_tail_cap_seconds",
                path,
                DEFAULT_COMPLETION_TAIL_CAP_SECONDS,
            ),
        )
    else:
        raise ProjectConfigError(f"{path} [dispatch.timeouts] must be a table")

    audit_raw = block.get("audit")
    if audit_raw is None:
        audit = AuditConfig()
    elif isinstance(audit_raw, dict):
        audit = AuditConfig(
            enabled=_bool(audit_raw, "enabled", path, True, prefix="dispatch.audit."),
            max_bytes=_positive_int(
                audit_raw, "max_bytes", path, DEFAULT_AUDIT_MAX_BYTES, prefix="dispatch.audit."
            ),
            redact_patterns=_str_tuple(
                audit_raw, "redact_patterns", path, prefix="dispatch.audit."
            ),
        )
    else:
        raise ProjectConfigError(f"{path} [dispatch.audit] must be a table")

    return DispatchConfig(timeouts=timeouts, audit=audit)


def _positive_number(mapping: dict[str, Any], key: str, path: Path, default: float) -> float:
    value = mapping.get(key, default)
    if not isinstance(value, int | float) or isinstance(value, bool) or value <= 0:
        raise ProjectConfigError(f"{path} dispatch.timeouts.{key} must be a positive number")
    return float(value)


def _non_negative_number(mapping: dict[str, Any], key: str, path: Path, default: float) -> float:
    value = mapping.get(key, default)
    if not isinstance(value, int | float) or isinstance(value, bool) or value < 0:
        raise ProjectConfigError(f"{path} dispatch.timeouts.{key} must be a non-negative number")
    return float(value)


def _review_valve(raw: dict[str, Any], path: Path) -> ReviewValveConfig:
    block = _table(raw, "review_valve", path)
    return ReviewValveConfig(
        every_n_work_units=_positive_int(
            block,
            "every_n_work_units",
            path,
            DEFAULT_REVIEW_EVERY_N_WORK_UNITS,
            prefix="review_valve.",
        ),
        end_of_epic=_bool(
            block, "end_of_epic", path, DEFAULT_REVIEW_END_OF_EPIC, prefix="review_valve."
        ),
    )


def _fix_rounds(raw: dict[str, Any], path: Path) -> FixRoundsConfig:
    block = _table(raw, "fix_rounds", path)
    return FixRoundsConfig(
        max_rounds_per_blocker=_non_negative_int(
            block,
            "max_rounds_per_blocker",
            path,
            DEFAULT_FIX_ROUNDS_PER_BLOCKER,
            prefix="fix_rounds.",
        )
    )


def _readiness(raw: dict[str, Any], path: Path) -> ReadinessConfig:
    block = _table(raw, "readiness", path)
    return ReadinessConfig(
        escalation_threshold=_positive_int(
            block,
            "escalation_threshold",
            path,
            DEFAULT_READINESS_ESCALATION_THRESHOLD,
            prefix="readiness.",
        )
    )


def _gates(raw: dict[str, Any], path: Path) -> tuple[GateSpec, ...]:
    block = _table(raw, "gates", path)
    specs: list[GateSpec] = []
    for name, body in block.items():
        if not isinstance(body, dict):
            raise ProjectConfigError(f"{path} gates.{name} must be a table")
        prefix = f"gates.{name}."
        mode = body.get("mode", "strict")
        if mode not in GATE_MODES:
            raise ProjectConfigError(
                f"{path} gates.{name}.mode must be one of {', '.join(GATE_MODES)}"
            )
        specs.append(
            GateSpec(
                name=name,
                command=_required_str(body, "command", path, prefix=prefix),
                timeout_seconds=_positive_int(
                    body, "timeout_seconds", path, DEFAULT_GATE_TIMEOUT_SECONDS, prefix=prefix
                ),
                blocking=_bool(body, "blocking", path, True, prefix=prefix),
                mode=mode,
            )
        )
    return tuple(specs)


def _prerequisites(raw: dict[str, Any], path: Path) -> PrerequisitesConfig:
    block = _table(raw, "prerequisites", path)
    lsp = block.get("lsp")
    if lsp is not None and not isinstance(lsp, dict):
        raise ProjectConfigError(f"{path} [prerequisites.lsp] must be a table")
    return PrerequisitesConfig(
        infra=_str_map(block, "infra", path),
        commands=_str_map(block, "commands", path),
        validators=_str_map(block, "validators", path),
        host=_sub_table(block, "host", path),
        servers=_sub_table(block, "servers", path),
        indexing=_sub_table(block, "indexing", path),
        lsp_languages=_str_tuple(lsp or {}, "languages", path, prefix="prerequisites.lsp."),
    )


def _str_map(block: dict[str, Any], name: str, path: Path) -> dict[str, str]:
    value = block.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ProjectConfigError(f"{path} [prerequisites.{name}] must be a table")
    return {str(key): str(item) for key, item in value.items()}


def _sub_table(block: dict[str, Any], name: str, path: Path) -> dict[str, Any]:
    value = block.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ProjectConfigError(f"{path} [prerequisites.{name}] must be a table")
    return value


def _tracker(raw: dict[str, Any], path: Path) -> TrackerConfig:
    block = _table(raw, "tracker", path)
    kind = block.get("kind")
    if kind not in TRACKER_KINDS:
        raise ProjectConfigError(f"{path} tracker.kind must be one of {', '.join(TRACKER_KINDS)}")
    repo = _optional_str(block, "repo", path, prefix="tracker.")
    if kind == "github" and not repo:
        raise ProjectConfigError(f'{path} tracker.kind = "github" requires a non-empty repo')
    return TrackerConfig(kind=kind, repo=repo)


def _test_markers(raw: dict[str, Any], path: Path) -> TestMarkersConfig:
    block = _table(raw, "test_markers", path)
    languages_raw = block.get("languages")
    if languages_raw is None:
        return TestMarkersConfig(languages={}, declared=False)
    if not isinstance(languages_raw, dict) or not languages_raw:
        raise ProjectConfigError(f"{path} [test_markers.languages] must be a non-empty table")
    languages: dict[str, TestMarkerLanguage] = {}
    for name, body in languages_raw.items():
        if not isinstance(body, dict):
            raise ProjectConfigError(f"{path} test_markers.languages.{name} must be a table")
        prefix = f"test_markers.languages.{name}."
        test_paths = _str_tuple(body, "test_paths", path, prefix=prefix)
        if not test_paths:
            raise ProjectConfigError(f"{path} {prefix}test_paths must list at least one path")
        languages[str(name)] = TestMarkerLanguage(
            name=str(name),
            test_paths=test_paths,
            marker_regex=_required_str(body, "marker_regex", path, prefix=prefix),
            cd_marker_regex=_required_str(body, "cd_marker_regex", path, prefix=prefix),
            docstring_keyword=_required_str(body, "docstring_keyword", path, prefix=prefix),
            comment_prefix=_required_str(body, "comment_prefix", path, prefix=prefix),
            context_lines=_non_negative_int(body, "context_lines", path, 3, prefix=prefix),
        )
    return TestMarkersConfig(languages=languages, declared=True)


def _docs_paths(raw: dict[str, Any], path: Path) -> DocsPathsConfig:
    block = _table(raw, "docs_paths", path)
    raw_mappings = block.get("mappings")
    if raw_mappings is None:
        return DocsPathsConfig(mappings=(), declared=False)
    if not isinstance(raw_mappings, list) or not raw_mappings:
        raise ProjectConfigError(f"{path} docs_paths.mappings must be a non-empty array")
    mappings: list[DocsPathMapping] = []
    for index, item in enumerate(raw_mappings):
        if not isinstance(item, dict):
            raise ProjectConfigError(f"{path} docs_paths.mappings[{index}] must be a table")
        prefix = f"docs_paths.mappings[{index}]."
        mappings.append(
            DocsPathMapping(
                code_pattern=_required_str(item, "code_pattern", path, prefix=prefix),
                doc_pattern=_required_str(item, "doc_pattern", path, prefix=prefix),
                rationale=_optional_str(item, "rationale", path, prefix=prefix),
            )
        )
    return DocsPathsConfig(mappings=tuple(mappings), declared=True)

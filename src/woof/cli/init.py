"""Project bootstrap for Woof (ADR-017).

``woof init --project <key>`` writes one config file into the operator home at
``~/.woof/config/projects/<key>.toml``. It writes nothing into the driven
repository: a delivery repo carries no trace of the engine that builds it.

The template uses explicit ``<replace>`` placeholders so nobody can run
preflight against unedited boilerplate. Init infers what it safely can from the
project's git remotes: with ``--tracker`` omitted it picks ``github``
(pre-filling ``repo`` as ``owner/name``) when an ``origin``/``upstream`` github
remote is reachable, otherwise ``local``. An explicit ``--tracker`` is always
honoured. Inference only ever replaces a placeholder with a real value, so init
stays fail-closed.

The one file init still writes into the repo is ``scripts/refresh-cartography``:
it is the project's own generator, invoked by the project's post-commit hook.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from woof.paths import ProjectKeyError, project_config_path, resolve_project_key, tool_root

# Bumped when the composed refresh-cartography body changes shape, so stamps from
# an older `woof init` are distinguishable (freshness.json.generator_version).
REFRESH_GENERATOR_VERSION = 2

GITHUB_REPO_PLACEHOLDER = "<replace>/<replace>"

PROJECT_CONFIG_TEMPLATE = """\
# Woof project config for `{project_key}`. Verified by `woof preflight`.
# Engine config lives in the operator home, never in the driven repo (ADR-017).
# Replace every <replace> placeholder before invoking `woof wf`.

schema_version = 1
type = "woof_project"
default_run_profile = "default"

[delivery]
profile = "B"
repo_root = "{repo_root}"
toolchain_root = "{repo_root}"
base_branch = "main"

[profiles.B]
commit = true
push = true

[verification]
command = "<replace project verification command, e.g. just check>"
timeout_seconds = 600

[run_profiles.default.producer]
harness = "codex"
model = "gpt-5.6-sol"
effort = "high"

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

# Cartography (ADR-004/ADR-013). `floor` decides whether preflight enforces
# none, design, lexical, or structural cartography; the remaining keys supply
# the details the non-none floors need.
[cartography]
floor = "design"
staleness_floor_hours = 168
summary_min_chars = 200
{cartography_languages}

[drain]
merge_after_ready_pr = false
rerun_after_merge = true
mark_unit_done_after_publish = true
commit_backlog_state = true
stop_when_no_eligible_units = true

# Runtime model: trusted-local automation. Woof does not sandbox dispatched
# agents, restrict writable paths, allow-list commands, block network access, or
# add MCP restrictions; commit-safety checks and gates guard what lands.
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

# Stage 5 Check 1 runs each gate from the delivery repository root. Blocking
# gates must exit 0 within `timeout_seconds`; set `blocking = false` to record
# a minor finding without failing Check 1.
[gates.test]
command = "<replace project test command, e.g. just test>"
timeout_seconds = 300

[prerequisites.infra]
just = "1.0+"
git = "2.30+"
{infra_gh}
[prerequisites.commands]
claude = "any"
codex = "any"

[prerequisites.validators]
ajv = "any"
ajv-formats = "any"

# Uncomment when the project uses LSP-backed reviewer context.
# [prerequisites.lsp]
# languages = ["python"]

{tracker_block}

# Stage 5 Check 2 outcome-marker rules per language. Woof ships Python and
# TypeScript defaults; declare [test_markers.languages.<lang>] only to override
# them or to add a language.

# Stage 5 Check 8 code-to-doc drift mappings. Check 8 is a no-op when
# [docs_paths] is absent; declare it only when the project wants enforced docs
# updates alongside specific code areas.
{docs_paths_block}"""

CARTOGRAPHY_LANGUAGES_HINT = (
    '# languages = ["python"]  # refresh-cartography fragments to compose (woof init --language)'
)

TRACKER_BLOCK_LOCAL = """\
# Issue tracker for epic-level contracts. kind = "local" keeps every epic on
# the filesystem with no remote, so any repository can run Woof without a
# hosted issue tracker. Re-run `woof init --tracker github` for a GitHub setup.
[tracker]
kind = "local\""""

DOCS_PATHS_BLOCK = """
[[docs_paths.mappings]]
code_pattern = "<replace, e.g. src/api/**/*.py>"
doc_pattern = "<replace, e.g. docs/api/**/*.md>"
rationale = "<replace with the reason this mapping exists>"
"""

# github remote URL forms init understands: scp-like ssh, ssh://, git://, and
# https (with an optional `user@`). The `owner/name` after the host is captured,
# with an optional `.git` suffix and trailing slash stripped.
_GITHUB_REMOTE_RE = re.compile(
    r"^(?:"
    r"git@github\.com:"
    r"|ssh://git@github\.com/"
    r"|git://github\.com/"
    r"|https://(?:[^@/]+@)?github\.com/"
    r")(?P<owner>[^/]+)/(?P<name>.+?)(?:\.git)?/?$"
)


def _tracker_block_github(repo: str) -> str:
    """Render the github ``[tracker]`` block with ``repo`` pre-filled.

    ``repo`` is the inferred ``owner/name`` slug, or ``GITHUB_REPO_PLACEHOLDER``
    when it could not be inferred from a git remote - the placeholder keeps init
    fail-closed so preflight refuses unedited boilerplate.
    """
    return (
        '# Issue tracker for epic-level contracts. kind = "github" keeps each epic in a\n'
        "# GitHub issue and needs `repo`. Re-run `woof init --tracker local` to scaffold\n"
        "# the local-only variant for a repository with no hosted issue tracker.\n"
        "[tracker]\n"
        'kind = "github"\n'
        f'repo = "{repo}"'
    )


def _parse_github_repo(url: str) -> str | None:
    """Extract an ``owner/name`` slug from a github remote URL, else None."""
    match = _GITHUB_REMOTE_RE.match(url.strip())
    if match is None:
        return None
    return f"{match.group('owner')}/{match.group('name')}"


def _git_remote_url(project_root: Path, remote: str) -> str | None:
    """Return the URL of ``remote`` in ``project_root``, or None.

    Tolerates a missing git binary, a non-repository directory, and an absent
    remote - every failure resolves to None so init falls back to the placeholder.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(project_root), "remote", "get-url", remote],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    url = proc.stdout.strip()
    return url or None


def _infer_github_repo(project_root: Path) -> str | None:
    """Infer the github ``owner/name`` slug from the project's git remotes.

    Checks ``origin`` then ``upstream`` and returns the first that parses as a
    github URL; None when no github remote is reachable, so the scaffold keeps
    its explicit ``<replace>`` placeholder.
    """
    for remote in ("origin", "upstream"):
        url = _git_remote_url(project_root, remote)
        if url is not None:
            slug = _parse_github_repo(url)
            if slug is not None:
                return slug
    return None


def _resolve_tracker(project_root: Path, tracker: str | None) -> tuple[str, str | None, bool]:
    """Resolve the tracker kind, github repo slug, and whether the kind was inferred.

    An explicit ``tracker`` (``github``/``local``) is honoured; an explicit github
    tracker still gets its ``repo`` inferred from the remote when reachable. With
    ``tracker`` omitted (None) the kind is inferred from the git remote: ``github``
    (plus slug) when a github remote is reachable, otherwise ``local``. Returns
    ``(resolved_kind, repo_slug, kind_inferred)``.
    """
    if tracker == "local":
        return "local", None, False
    if tracker == "github":
        return "github", _infer_github_repo(project_root), False
    slug = _infer_github_repo(project_root)
    if slug is not None:
        return "github", slug, True
    return "local", None, True


def _cartography_languages_line(languages: list[str]) -> str:
    """Render the ``[cartography].languages`` line for the scaffolded template.

    With declared languages it emits an active ``languages = [...]`` array that
    drives ``woof init`` script composition; with none it leaves the commented
    hint so the block stays valid and self-documenting.
    """
    if not languages:
        return CARTOGRAPHY_LANGUAGES_HINT
    rendered = ", ".join(f'"{language}"' for language in languages)
    return f"languages = [{rendered}]"


def project_config_template(
    project_key: str,
    tracker_kind: str,
    languages: list[str],
    repo_slug: str | None = None,
    *,
    with_docs_paths: bool = False,
    repo_root: str = ".",
) -> str:
    """Render the project config for the chosen tracker.

    The github tracker declares `gh` as required infra; the local tracker omits
    it so a project with no hosted issue tracker is not forced to install it.
    ``repo_slug`` pre-fills the github ``repo`` line when inferred from a git
    remote; without it the ``<replace>`` placeholder is kept.
    """

    tracker_block = (
        TRACKER_BLOCK_LOCAL
        if tracker_kind == "local"
        else _tracker_block_github(repo_slug or GITHUB_REPO_PLACEHOLDER)
    )
    return PROJECT_CONFIG_TEMPLATE.format(
        project_key=project_key,
        repo_root=repo_root,
        infra_gh='gh = "2.0+"\n' if tracker_kind == "github" else "",
        tracker_block=tracker_block,
        cartography_languages=_cartography_languages_line(languages),
        docs_paths_block=DOCS_PATHS_BLOCK if with_docs_paths else "",
    )


# --- Cartography refresh script composition (ADR-004, E1/S3) ----------------
#
# `woof init` composes scripts/refresh-cartography from a shared scaffold plus
# the per-language fragments declared in languages/<lang>.toml. The shared
# scaffold owns the mechanical layer (git ls-files -> files.txt; a single ctags
# pass -> tags; the freshness.json stamp); each fragment registers its ctags
# language so the one ctags pass covers exactly the declared languages. The body
# lives in a managed block, mirroring the post-commit hook idiom, so re-running
# init replaces the block in place rather than duplicating it.

REFRESH_SCRIPT_RELPATH = "scripts/refresh-cartography"
REFRESH_SHEBANG = "#!/usr/bin/env sh"
REFRESH_BEGIN = "# >>> woof:refresh-cartography"
REFRESH_END = "# <<< woof:refresh-cartography"
REFRESH_BLOCK_RE = re.compile(rf"(?ms)^{re.escape(REFRESH_BEGIN)}\n.*?^{re.escape(REFRESH_END)}\n?")

# The scaffold uses literal shell braces, so it is assembled by token
# substitution rather than str.format to avoid brace-escaping noise.
REFRESH_SCAFFOLD = """\
# >>> woof:refresh-cartography
# Managed by `woof init`; regenerates the .woof/codebase mechanical layer
# (files.txt, tags, freshness.json). Re-run `woof init --language <lang> ...` to
# recompose. Edits inside this block are overwritten.
set -eu

woof_root=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
cd "$woof_root"
woof_codebase=".woof/codebase"
mkdir -p "$woof_codebase"

# git ls-files -> files.txt
git ls-files >"$woof_codebase/files.txt"

# Per-language ctags coverage, contributed by the composed fragments below.
woof_ctags_languages=""
woof_add_ctags_language() {
  if [ -z "$woof_ctags_languages" ]; then
    woof_ctags_languages="$1"
  else
    woof_ctags_languages="$woof_ctags_languages,$1"
  fi
}

__WOOF_FRAGMENTS__

# ctags -> tags, scoped to the declared cartography languages. ctags is a hard
# cartography prerequisite (ADR-004); refresh fails loud when ctags is absent
# and languages are declared rather than silently writing an empty index.
if [ -n "$woof_ctags_languages" ]; then
  if ! command -v ctags >/dev/null 2>&1; then
    echo "woof refresh-cartography: ctags not found on PATH; install universal-ctags" >&2
    echo "  Debian/Ubuntu: sudo apt install -y universal-ctags" >&2
    echo "  macOS:          brew install universal-ctags" >&2
    echo "  Arch/CachyOS:   sudo pacman -S ctags" >&2
    exit 1
  fi
  ctags --languages="$woof_ctags_languages" -L "$woof_codebase/files.txt" -f "$woof_codebase/tags"
fi

# freshness.json stamp: {ts, git_ref, age_s, generator_version}. ts is the
# authoritative staleness signal; age_s is 0 at generation and never advances.
woof_ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
woof_git_ref=$(git rev-parse HEAD 2>/dev/null || echo unknown)
printf '{"ts":"%s","git_ref":"%s","age_s":%d,"generator_version":%d}\\n' \\
  "$woof_ts" "$woof_git_ref" 0 __WOOF_GENERATOR_VERSION__ >"$woof_codebase/freshness.json"
# <<< woof:refresh-cartography
"""


class InitError(RuntimeError):
    """Raised when init cannot write a project config or compose a script."""


@dataclass(frozen=True)
class FileAction:
    relpath: str
    action: str  # "created", "updated", "skipped"
    reason: str | None = None


@dataclass(frozen=True)
class InitResult:
    project_root: Path
    project_key: str
    config: FileAction
    tracker: str = "github"
    script: FileAction | None = None
    script_note: str | None = None
    languages: tuple[str, ...] = ()
    inferred_repo: str | None = None
    tracker_inferred: bool = False

    @property
    def changed(self) -> bool:
        config_changed = self.config.action in {"created", "updated"}
        script_changed = self.script is not None and self.script.action in {"created", "updated"}
        return config_changed or script_changed


def cmd_init(args: argparse.Namespace) -> int:
    project_root = _resolve_project_root(args.project_root)
    if not project_root.is_dir():
        sys.stderr.write(f"woof: {project_root}: not a directory\n")
        return 2

    try:
        project_key = resolve_project_key(args.project)
        result = run_init(
            project_root,
            project_key=project_key,
            force=args.force,
            with_docs_paths=args.with_docs_paths,
            tracker=args.tracker,
            languages=args.language,
        )
    except (InitError, ProjectKeyError) as exc:
        sys.stderr.write(f"woof: {exc}\n")
        return 2
    _print_result(result)
    return 0


def setup_init_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    project: argparse.ArgumentParser,
) -> None:
    init = subparsers.add_parser(
        "init",
        help="write a project config into the operator home",
        parents=[project],
    )
    init.add_argument(
        "--project-root",
        help="delivery checkout the config describes; defaults to the current directory",
    )
    init.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing project config instead of refusing",
    )
    init.add_argument(
        "--with-docs-paths",
        action="store_true",
        help="also scaffold the [docs_paths] section (Stage 5 Check 8 mappings)",
    )
    init.add_argument(
        "--tracker",
        choices=["github", "local"],
        default=None,
        help=(
            "issue tracker to scaffold. Omit to infer from the git remote: github "
            "(with repo pre-filled) when an origin/upstream github remote is "
            "reachable, otherwise local."
        ),
    )
    init.add_argument(
        "--language",
        action="append",
        default=None,
        metavar="LANG",
        help=(
            "cartography language to compose into scripts/refresh-cartography "
            "(repeatable). Writes [cartography].languages and composes the script."
        ),
    )
    init.set_defaults(func=cmd_init)


def run_init(
    project_root: Path,
    *,
    project_key: str,
    force: bool = False,
    with_docs_paths: bool = False,
    tracker: str | None = None,
    languages: list[str] | None = None,
) -> InitResult:
    requested = _normalise_languages(languages)
    _validate_cartography_languages(requested)

    resolved_tracker, inferred_repo, tracker_inferred = _resolve_tracker(project_root, tracker)

    config_path = project_config_path(project_key)
    if config_path.is_file() and not force:
        raise InitError(
            f"{config_path} already exists; refusing to overwrite it. "
            "Edit it, or re-run with --force to replace it."
        )
    existed = config_path.is_file()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        project_config_template(
            project_key,
            resolved_tracker,
            requested,
            inferred_repo,
            with_docs_paths=with_docs_paths,
        ),
        encoding="utf-8",
    )
    config = FileAction(
        relpath=str(config_path), action="updated" if existed else "created", reason=None
    )

    script: FileAction | None = None
    script_note: str | None = None
    if requested:
        script = _compose_refresh_script(project_root, requested)
    else:
        script_note = (
            f"skipped {REFRESH_SCRIPT_RELPATH}: no cartography languages declared "
            "(pass --language <lang>)"
        )

    return InitResult(
        project_root=project_root,
        project_key=project_key,
        config=config,
        tracker=resolved_tracker,
        script=script,
        script_note=script_note,
        languages=tuple(requested),
        inferred_repo=inferred_repo,
        tracker_inferred=tracker_inferred,
    )


def _normalise_languages(languages: list[str] | None) -> list[str]:
    """De-duplicate requested languages while preserving first-seen order."""
    seen: dict[str, None] = {}
    for language in languages or []:
        seen.setdefault(language.strip(), None)
    seen.pop("", None)
    return list(seen)


def _available_cartography_languages() -> list[str]:
    """Languages whose registry declares a [cartography].refresh_fragment."""
    languages_dir = tool_root() / "languages"
    available: list[str] = []
    for path in sorted(languages_dir.glob("*.toml")):
        try:
            with path.open("rb") as fh:
                data = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        if (data.get("cartography") or {}).get("refresh_fragment"):
            available.append(path.stem)
    return available


def _validate_cartography_languages(languages: list[str]) -> None:
    available = set(_available_cartography_languages())
    unknown = [language for language in languages if language not in available]
    if unknown:
        listed = ", ".join(sorted(available)) or "(none)"
        raise InitError(
            f"unknown cartography language(s): {', '.join(unknown)}. "
            f"Languages with a refresh-cartography fragment: {listed}"
        )


def _refresh_fragment_text(language: str) -> str:
    """Read one language's refresh-cartography fragment from its registry."""
    languages_dir = tool_root() / "languages"
    registry_path = languages_dir / f"{language}.toml"
    if not registry_path.is_file():
        raise InitError(f"no language registry at {registry_path}")
    with registry_path.open("rb") as fh:
        data = tomllib.load(fh)
    fragment_rel = (data.get("cartography") or {}).get("refresh_fragment")
    if not fragment_rel:
        raise InitError(f"{language}: registry declares no [cartography].refresh_fragment")
    fragment_path = languages_dir / fragment_rel
    if not fragment_path.is_file():
        raise InitError(f"{language}: refresh fragment not found at {fragment_path}")
    return fragment_path.read_text().strip("\n")


def _render_refresh_block(languages: list[str]) -> str:
    """Render the managed refresh-cartography block for the given languages."""
    fragments = "\n".join(_refresh_fragment_text(language) for language in languages)
    return REFRESH_SCAFFOLD.replace("__WOOF_FRAGMENTS__", fragments).replace(
        "__WOOF_GENERATOR_VERSION__", str(REFRESH_GENERATOR_VERSION)
    )


def _compose_refresh_body(existing: str | None, block: str) -> str:
    """Insert or replace the managed block, mirroring the post-commit hook idiom.

    The replacement uses a function so ``re.sub`` does not interpret the
    backslash escapes inside the composed shell body (the freshness ``printf``
    carries a literal ``\\n``); a plain string replacement would mangle them and
    break idempotency on re-compose.
    """
    if existing is None or existing == "":
        return f"{REFRESH_SHEBANG}\n\n{block}"
    if REFRESH_BLOCK_RE.search(existing):
        return REFRESH_BLOCK_RE.sub(lambda _match: block, existing, count=1)
    separator = "\n" if existing.endswith("\n") else "\n\n"
    return f"{existing}{separator}{block}"


def _compose_refresh_script(project_root: Path, languages: list[str]) -> FileAction:
    """Compose scripts/refresh-cartography idempotently and make it executable."""
    block = _render_refresh_block(languages)
    script_path = project_root / REFRESH_SCRIPT_RELPATH
    existing = script_path.read_text() if script_path.is_file() else None
    updated = _compose_refresh_body(existing, block)

    if existing == updated:
        action = "skipped"
    else:
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(updated)
        action = "updated" if existing is not None else "created"
    script_path.chmod(0o755)
    return FileAction(relpath=REFRESH_SCRIPT_RELPATH, action=action)


def _resolve_project_root(project_root: str | None) -> Path:
    return Path(project_root or ".").resolve()


def _print_result(result: InitResult) -> None:
    print(f"woof init: project {result.project_key} (tracker: {result.tracker})")
    suffix = f" ({result.config.reason})" if result.config.reason else ""
    print(f"  {result.config.action:<8} {result.config.relpath}{suffix}")
    if result.tracker_inferred and result.tracker == "local":
        print(
            "  note     inferred tracker: local "
            "(no github remote found; pass --tracker github to override)"
        )
    if result.inferred_repo is not None:
        prefix = "inferred tracker: github, " if result.tracker_inferred else ""
        print(f"  note     {prefix}repo = {result.inferred_repo} (from git remote)")
    if result.script is not None:
        print(f"  {result.script.action:<8} {result.script.relpath}")
    if result.script_note:
        print(f"  note     {result.script_note}")
    print()
    print("Next steps:")
    print(f"  1. Replace the <replace> placeholders in {result.config.relpath}.")
    print("  2. Authenticate the model CLIs once: `claude /login` and `codex login`.")
    print(f"  3. Run `woof preflight --project {result.project_key}` and resolve any failures.")
    print("  4. Run `woof hooks install` to enable the post-commit cartography hook.")
    print(f'  5. Start your first epic: `woof wf new "<spark>" --project {result.project_key}`.')
    print("  6. Run the graph with the command printed by `woof wf new`.")
    print()
    print("See skills/woof/references/setup.md for the full first-run walkthrough.")

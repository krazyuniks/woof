"""Consumer bootstrap for Woof projects.

``woof init`` scaffolds a fresh ``.woof/`` directory and the matching
``.gitignore`` entries so a stranger checking Woof out against their own repo
does not have to hand-assemble four schema-bound TOMLs and remember every
required gitignore line. The templates use explicit ``<replace>`` placeholders
so a consumer cannot accidentally run preflight against unedited boilerplate.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

PREREQUISITES_TEMPLATE = """\
# Woof project prerequisites. Verified by `woof preflight`.
# Replace every <replace> placeholder before invoking `woof wf`.

[infra]
just = "1.0+"
git = "2.30+"
{infra_gh}
[commands]
claude = "any"
codex = "any"

[validators]
ajv = "any"
ajv-formats = "any"

{tracker_block}

# Uncomment when the project uses LSP-backed reviewer context.
# [lsp]
# languages = ["python"]
"""

TRACKER_BLOCK_GITHUB = """\
# Issue tracker for epic-level contracts. kind = "github" keeps each epic in a
# GitHub issue and needs `repo`. Re-run `woof init --tracker local` to scaffold
# the local-only variant for a repository with no hosted issue tracker.
[tracker]
kind = "github"
repo = "<replace>/<replace>\""""

TRACKER_BLOCK_LOCAL = """\
# Issue tracker for epic-level contracts. kind = "local" keeps every epic under
# .woof/epics/E<N>/ with no remote, so any repository can run Woof without a
# hosted issue tracker. Re-run `woof init --tracker github` for a GitHub setup.
[tracker]
kind = "local\""""


def _prerequisites_template(tracker_kind: str) -> str:
    """Render prerequisites.toml for the chosen tracker.

    The github tracker declares `gh` as required infra; the local tracker omits
    it so a consumer with no hosted issue tracker is not forced to install it.
    """
    if tracker_kind == "local":
        return PREREQUISITES_TEMPLATE.format(infra_gh="", tracker_block=TRACKER_BLOCK_LOCAL)
    return PREREQUISITES_TEMPLATE.format(
        infra_gh='gh = "2.0+"\n', tracker_block=TRACKER_BLOCK_GITHUB
    )


AGENTS_TEMPLATE = """\
# Woof role routes. ADR-002: GPT-5.5 is the preferred primary route; Claude
# Opus 4.7 at `max` effort is the preferred reviewer route. Reviewer blockers
# open human gates; do not add model-to-model debate loops.
# Runtime model: trusted-local automation. Woof does not sandbox dispatched
# agents, restrict writable paths, allow-list commands, block network access, or
# add MCP restrictions; commit-safety checks and gates guard what lands.

[roles.primary]
adapter = "codex"
model = "gpt-5.5"
effort = "xhigh"

[roles.reviewer]
adapter = "claude"
model = "claude-opus-4-7"
effort = "max"
mcp = []

[roles.orchestrator]
adapter = "in-session"

[roles.gate-resolver]
adapter = "in-session"

[timeouts]
default_minutes = 30

[review_valve]
every_n_stories = 5
end_of_epic = true

[audit]
enabled = true
max_bytes = 262144
redact_patterns = []
"""

QUALITY_GATES_TEMPLATE = """\
# Stage 5 Check 1 runs each gate from the consumer repository root. Blocking
# gates must exit 0 within `timeout_seconds`; set `blocking = false` to record
# a minor finding without failing Check 1.

[gates.test]
command = "<replace project test command, e.g. just test>"
timeout_seconds = 300
"""

TEST_MARKERS_TEMPLATE = """\
# Stage 5 Check 2 outcome-marker rules per language. Defaults ship for Python
# and TypeScript; add or override languages here when the project uses other
# test layouts or marker conventions.

[languages.python]
test_paths = ["tests/", "src/**/test_*.py"]
marker_regex = '(?<![A-Za-z0-9])O\\d+(?![A-Za-z0-9])'
cd_marker_regex = '(?<![A-Za-z0-9])CD\\d+(?![A-Za-z0-9])'
docstring_keyword = "outcomes:"
comment_prefix = "#"
context_lines = 3

[languages.typescript]
test_paths = ["tests/", "src/**/*.test.ts"]
marker_regex = '(?<![A-Za-z0-9])O\\d+(?![A-Za-z0-9])'
cd_marker_regex = '(?<![A-Za-z0-9])CD\\d+(?![A-Za-z0-9])'
docstring_keyword = "outcomes:"
comment_prefix = "//"
context_lines = 3
"""

DOCS_PATHS_TEMPLATE = """\
# Stage 5 Check 8 code-to-doc drift mappings. Stage 5 Check 8 is a no-op when
# this file is absent; populate it only when the project wants enforced docs
# updates alongside specific code areas.

[[mappings]]
code_pattern = "<replace, e.g. src/api/**/*.py>"
doc_pattern = "<replace, e.g. docs/api/**/*.md>"
rationale = "<replace with the reason this mapping exists>"
"""

GITIGNORE_BEGIN = "# >>> woof"
GITIGNORE_END = "# <<< woof"
GITIGNORE_ENTRIES = [
    ".woof/.current-epic",
    ".woof/epics/*/gate.md",
    ".woof/epics/*/.wf.lock",
    ".woof/epics/*/.last-sync",
    ".woof/epics/*/executor_result.json",
    ".woof/epics/*/check-result.json",
    ".woof/epics/*/audit/raw/",
    ".woof/codebase/tags",
    ".woof/codebase/tree.txt",
    ".woof/codebase/freshness.json",
    ".woof/.preflight-floor",
    ".woof/.preflight-runtime",
]
GITIGNORE_BLOCK = "\n".join(
    [
        GITIGNORE_BEGIN,
        "# Managed by `woof init`; runtime/per-worktree state that must not be committed.",
        *GITIGNORE_ENTRIES,
        GITIGNORE_END,
    ]
)
GITIGNORE_BLOCK_RE = re.compile(
    rf"(?ms)^{re.escape(GITIGNORE_BEGIN)}\n.*?^{re.escape(GITIGNORE_END)}\n?"
)


@dataclass(frozen=True)
class FileAction:
    relpath: str
    action: str  # "created", "updated", "skipped"
    reason: str | None = None


@dataclass(frozen=True)
class InitResult:
    project_root: Path
    files: list[FileAction]
    gitignore_changed: bool
    tracker: str = "github"

    @property
    def changed(self) -> bool:
        return any(f.action in {"created", "updated"} for f in self.files) or self.gitignore_changed


def cmd_init(args: argparse.Namespace) -> int:
    project_root = _resolve_project_root(args.project_root)
    if not project_root.is_dir():
        sys.stderr.write(f"woof: {project_root}: not a directory\n")
        return 2

    result = run_init(
        project_root,
        force=args.force,
        with_docs_paths=args.with_docs_paths,
        tracker=args.tracker,
    )
    _print_result(result)
    return 0


def setup_init_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    init = subparsers.add_parser(
        "init",
        help="scaffold a .woof/ consumer config and required .gitignore entries",
    )
    init.add_argument(
        "--project-root",
        help="consumer project root to initialise; defaults to the current directory",
    )
    init.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing .woof/ TOML files instead of skipping them",
    )
    init.add_argument(
        "--with-docs-paths",
        action="store_true",
        help="also scaffold .woof/docs-paths.toml (Stage 5 Check 8 mappings)",
    )
    init.add_argument(
        "--tracker",
        choices=["github", "local"],
        default="github",
        help="issue tracker to scaffold in prerequisites.toml (default: github)",
    )
    init.set_defaults(func=cmd_init)


def run_init(
    project_root: Path,
    *,
    force: bool = False,
    with_docs_paths: bool = False,
    tracker: str = "github",
) -> InitResult:
    woof_dir = project_root / ".woof"
    woof_dir.mkdir(exist_ok=True)

    files: list[FileAction] = []
    targets: list[tuple[str, str]] = [
        ("prerequisites.toml", _prerequisites_template(tracker)),
        ("agents.toml", AGENTS_TEMPLATE),
        ("quality-gates.toml", QUALITY_GATES_TEMPLATE),
        ("test-markers.toml", TEST_MARKERS_TEMPLATE),
    ]
    if with_docs_paths:
        targets.append(("docs-paths.toml", DOCS_PATHS_TEMPLATE))

    for name, content in targets:
        target = woof_dir / name
        relpath = f".woof/{name}"
        if target.is_file() and not force:
            files.append(FileAction(relpath=relpath, action="skipped", reason="already exists"))
            continue
        existed = target.is_file()
        target.write_text(content)
        files.append(
            FileAction(relpath=relpath, action="updated" if existed else "created", reason=None)
        )

    gitignore_changed = _update_gitignore(project_root)
    return InitResult(
        project_root=project_root,
        files=files,
        gitignore_changed=gitignore_changed,
        tracker=tracker,
    )


def _resolve_project_root(project_root: str | None) -> Path:
    return Path(project_root or ".").resolve()


def _update_gitignore(project_root: Path) -> bool:
    path = project_root / ".gitignore"
    existing = path.read_text() if path.is_file() else ""

    if GITIGNORE_BLOCK_RE.search(existing):
        updated = GITIGNORE_BLOCK_RE.sub(GITIGNORE_BLOCK + "\n", existing, count=1)
    else:
        separator = "" if existing == "" else ("\n" if existing.endswith("\n") else "\n\n")
        updated = existing + separator + GITIGNORE_BLOCK + "\n"

    if updated == existing:
        return False
    path.write_text(updated)
    return True


def _print_result(result: InitResult) -> None:
    print(f"woof init: {result.project_root} (tracker: {result.tracker})")
    for action in result.files:
        suffix = f" ({action.reason})" if action.reason else ""
        print(f"  {action.action:<8} {action.relpath}{suffix}")
    if result.gitignore_changed:
        print("  updated  .gitignore (woof block)")
    else:
        print("  current  .gitignore (woof block already present)")
    print()
    print("Next steps:")
    print("  1. Replace every <replace> placeholder in .woof/*.toml.")
    print("  2. Authenticate the model CLIs once: `claude /login` and `codex login`.")
    print("  3. Run `woof preflight` and resolve any remaining failures.")
    print("  4. Run `woof hooks install` to enable the post-commit cartography hook.")
    print('  5. Start your first epic: `woof wf new "<spark>"`.')
    print("  6. Run the graph with the command printed by `woof wf new`.")
    print()
    print("See docs/consumers.md for the full first-run walkthrough.")

"""Git hook installation helpers for Woof projects."""

from __future__ import annotations

import argparse
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

HOOK_BLOCK_NAME = "woof-cartography"
HOOK_BEGIN = f"# >>> {HOOK_BLOCK_NAME}"
HOOK_END = f"# <<< {HOOK_BLOCK_NAME}"
HOOK_BODY = "[ -x ./scripts/refresh-cartography ] && ./scripts/refresh-cartography"
HOOK_BLOCK = f"{HOOK_BEGIN}\n{HOOK_BODY}\n{HOOK_END}\n"
HOOK_BLOCK_RE = re.compile(rf"(?ms)^{re.escape(HOOK_BEGIN)}\n.*?^{re.escape(HOOK_END)}\n?")


@dataclass(frozen=True)
class HookInstallResult:
    hook_path: Path
    changed: bool


def cmd_hooks(args: argparse.Namespace) -> int:
    if args.hooks_command == "install":
        repo_root = _resolve_project_root(args.project_root)
        try:
            result = install_woof_hooks(repo_root)
        except HookInstallError as exc:
            sys.stderr.write(f"woof: {exc}\n")
            return 2
        status = "installed" if result.changed else "already installed"
        print(f"woof hooks: {status}: {result.hook_path}")
        return 0
    sys.stderr.write("woof: missing hooks subcommand\n")
    return 2


def setup_hooks_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    hooks = subparsers.add_parser("hooks", help="manage Woof-managed git hooks")
    hooks_sub = hooks.add_subparsers(dest="hooks_command", required=True)

    install = hooks_sub.add_parser("install", help="install Woof-managed git hooks")
    install.add_argument(
        "--project-root",
        help="git project root to install into; defaults to the current directory",
    )
    install.set_defaults(func=cmd_hooks)


class HookInstallError(RuntimeError):
    """Raised when hook installation cannot target a git checkout."""


def install_woof_hooks(repo_root: Path) -> HookInstallResult:
    hook_path = _git_hook_path(repo_root, "post-commit")
    hook_path.parent.mkdir(parents=True, exist_ok=True)

    existing = hook_path.read_text() if hook_path.is_file() else None
    updated = _append_managed_block(existing)
    if existing == updated:
        _ensure_executable(hook_path)
        return HookInstallResult(hook_path=hook_path, changed=False)

    hook_path.write_text(updated)
    _ensure_executable(hook_path)
    return HookInstallResult(hook_path=hook_path, changed=True)


def _resolve_project_root(raw: str | None) -> Path:
    return Path(raw or ".").resolve()


def _git_hook_path(repo_root: Path, hook_name: str) -> Path:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--git-path", f"hooks/{hook_name}"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip() or f"{repo_root} is not a git checkout"
        raise HookInstallError(detail)
    raw_path = proc.stdout.strip()
    if not raw_path:
        raise HookInstallError("git did not return a hook path")
    path = Path(raw_path)
    return path if path.is_absolute() else (repo_root / path).resolve()


def _append_managed_block(existing: str | None) -> str:
    if existing is None or existing == "":
        return f"#!/usr/bin/env sh\n\n{HOOK_BLOCK}"

    if HOOK_BLOCK_RE.search(existing):
        return HOOK_BLOCK_RE.sub(HOOK_BLOCK, existing, count=1)

    separator = "\n" if existing.endswith("\n") else "\n\n"
    return f"{existing}{separator}{HOOK_BLOCK}"


def _ensure_executable(path: Path) -> None:
    mode = path.stat().st_mode
    executable_bits = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    os.chmod(path, mode | executable_bits)

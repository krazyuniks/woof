"""Repo-local Woof policy helpers."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

POLICY_FILENAME = "policy.toml"
POLICY_RELPATH = f".woof/{POLICY_FILENAME}"

DELIVERY_PROFILES = {"A", "B"}
CARTOGRAPHY_FLOORS = {"none", "design", "lexical", "structural"}
CHECK_FLOOR_IDS = {
    "quality-gates",
    "outcome-markers",
    "scope",
    "contract-refs",
    "plan-crossrefs",
    "critique-blocker",
    "commit-transaction",
    "docs-drift",
    "review-valve",
}
RUN_PROFILE_ROLES = ("producer", "reviewer")


def policy_path(repo_root: Path) -> Path:
    return repo_root / POLICY_RELPATH


def load_policy(repo_root: Path) -> dict[str, Any] | str:
    path = policy_path(repo_root)
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        return f"{path} not found"
    except tomllib.TOMLDecodeError as exc:
        return f"{path}: TOML parse error: {exc}"


def cartography_floor(policy: dict[str, Any] | None) -> str | None:
    if not isinstance(policy, dict):
        return None
    cartography = policy.get("cartography")
    if not isinstance(cartography, dict):
        return None
    floor = cartography.get("floor")
    if isinstance(floor, str) and floor:
        return floor
    return None

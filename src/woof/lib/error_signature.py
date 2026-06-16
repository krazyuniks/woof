"""Deterministic error-signature normalisation for dispatch telemetry."""

from __future__ import annotations

import re

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
_PATH_RE = re.compile(
    r"(?:[A-Za-z]:)?/[^\s\"'<>|,;:()\[\]{}!?]+"  # absolute (inc. Windows drive)
    r"|\.\.?/[^\s\"'<>|,;:()\[\]{}!?]+"  # ./rel or ../rel
    r"|(?<!\w)[A-Za-z][A-Za-z0-9_.\-]*/[^\s\"'<>|,;:()\[\]{}!?]*"  # bare relative: src/foo.py
    r"|(?<!\w)[A-Za-z][A-Za-z0-9_\-]*\.[A-Za-z]{2,6}"  # bare filename: foo.py, main.js
)

# Positional span forms — only stripped in recognised structural contexts.
# :L or :L:C immediately following a <path> placeholder (handles foo.py:42:10 residue).
_SPAN_AFTER_PATH_RE = re.compile(r"(?<=<path>)(?::\d+)+")
# "line L" or "line L, col C" (word-boundary anchored, case-insensitive).
_LINE_FORM_RE = re.compile(r"\bline\s+\d+(?:\s*,\s*col\s+\d+)?\b", re.IGNORECASE)
# (L,C) parenthesised pair — only immediately after a <path> placeholder to avoid
# stripping meaningful numeric tuples such as shape (2, 3) in error messages.
_PAREN_LC_RE = re.compile(r"(<path>)\s*\(\s*\d+\s*,\s*\d+\s*\)")
# [L:C] bracket pair — only immediately after a <path> placeholder to avoid
# stripping slice notation such as [1:2] from IndexError messages.
_BRACKET_LC_RE = re.compile(r"(<path>)\s*\[\s*\d+\s*:\s*\d+\s*\]")
# Whitespace left between <path> and a following colon after span removal.
_PATH_COLON_WS_RE = re.compile(r"(?<=<path>)\s+(?=:)")

_EXCESS_WS_RE = re.compile(r"\s+")

MAX_LEN = 256


def normalise(text: str) -> str:
    """Return a normalised, bounded, deterministic signature of an error string.

    Strips volatile content so the same logical error from different runs
    produces the same signature regardless of paths, timestamps, or identifiers.
    Standalone integers (e.g. exit codes, error counts) are preserved.
    Only recognised positional span forms are stripped; key:value pairs
    like exit_code:1 are left intact.
    """
    sig = _UUID_RE.sub("<uuid>", text)
    sig = _TIMESTAMP_RE.sub("<ts>", sig)
    sig = _PATH_RE.sub("<path>", sig)
    sig = _SPAN_AFTER_PATH_RE.sub("", sig)
    sig = _LINE_FORM_RE.sub("", sig)
    sig = _PAREN_LC_RE.sub(r"\1", sig)
    sig = _BRACKET_LC_RE.sub(r"\1", sig)
    sig = _PATH_COLON_WS_RE.sub("", sig)
    sig = _EXCESS_WS_RE.sub(" ", sig).strip()
    return sig[:MAX_LEN]

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
)
_LINE_COL_RE = re.compile(r":\d+(?::\d+)*")
_EXCESS_WS_RE = re.compile(r"\s+")

MAX_LEN = 256


def normalise(text: str) -> str:
    """Return a normalised, bounded, deterministic signature of an error string.

    Strips volatile content so the same logical error from different runs
    produces the same signature regardless of paths, timestamps, or identifiers.
    Standalone integers (e.g. exit codes, error counts) are preserved.
    """
    sig = _UUID_RE.sub("<uuid>", text)
    sig = _TIMESTAMP_RE.sub("<ts>", sig)
    sig = _PATH_RE.sub("<path>", sig)
    sig = _LINE_COL_RE.sub("", sig)
    sig = _EXCESS_WS_RE.sub(" ", sig).strip()
    return sig[:MAX_LEN]

"""Rate-limit signal classification for dispatch telemetry."""

from __future__ import annotations

_MARKERS = frozenset(
    {
        "rate limit",
        "rate_limit",
        "rate-limit",
        "ratelimit",
        "too many requests",
        "429",
        "overloaded",
        "quota exceeded",
        "quota_exceeded",
        "resource_exhausted",
    }
)


def classify(stdout: str, stderr: str) -> str | None:
    """Return 'rate_limited' if adapter output signals rate-limiting, else None."""
    combined = (stdout + "\n" + stderr).lower()
    for marker in _MARKERS:
        if marker in combined:
            return "rate_limited"
    return None

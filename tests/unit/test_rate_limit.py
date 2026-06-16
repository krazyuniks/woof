"""Tests for rate-limit signal classification."""

from __future__ import annotations

from woof.lib.rate_limit import classify


def test_bare_429_in_line_not_classified() -> None:
    assert classify("line 429 is problematic", "") is None


def test_bare_429_digit_prefix_not_classified() -> None:
    assert classify("error 4290 occurred", "") is None


def test_http_429_classified() -> None:
    assert classify("", "HTTP 429 Too Many Requests") == "rate_limited"


def test_status_429_classified() -> None:
    assert classify("", "received status 429 from server") == "rate_limited"


def test_rate_limit_phrase_classified() -> None:
    assert classify("", "rate limit exceeded, retry later") == "rate_limited"


def test_too_many_requests_classified() -> None:
    assert classify("", "too many requests") == "rate_limited"


def test_clean_output_not_classified() -> None:
    assert classify("all good\n", "") is None


def test_quota_exceeded_classified() -> None:
    assert classify("", "quota exceeded") == "rate_limited"

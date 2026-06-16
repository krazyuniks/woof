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


# --- Anchored classification tests (R2) ---


def test_no_rate_limit_not_classified() -> None:
    assert classify("no rate limit was hit", "") is None


def test_overloaded_function_not_classified() -> None:
    assert classify("overloaded function", "") is None


def test_status_4290_not_classified() -> None:
    assert classify("status 4290 occurred", "") is None


def test_rate_limited_retry_classified() -> None:
    assert classify("HTTP 429 Too Many Requests", "rate limited, retry after 30s") == "rate_limited"


def test_rate_limit_none_not_classified() -> None:
    assert classify("rate limit: none", "") is None


def test_no_http_429_not_classified() -> None:
    assert classify("no HTTP 429 observed", "") is None


def test_no_status_429_not_classified() -> None:
    assert classify("no status 429 errors", "") is None


def test_no_http_status_429_not_classified() -> None:
    assert classify("no HTTP status 429 encountered", "") is None


def test_no_error_code_429_not_classified() -> None:
    assert classify("no error code 429", "") is None

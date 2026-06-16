"""Tests for error-signature normalisation (O3 dispatch telemetry)."""

from __future__ import annotations

from woof.lib.error_signature import MAX_LEN, normalise


def test_absolute_path_stripped() -> None:
    sig = normalise("error reading /home/user/project/src/foo.py")
    assert "/home" not in sig
    assert "<path>" in sig


def test_relative_path_stripped() -> None:
    sig = normalise("cannot open ./src/bar.py")
    assert "./src" not in sig
    assert "<path>" in sig


def test_parent_relative_path_stripped() -> None:
    sig = normalise("not found ../config/settings.toml")
    assert "../config" not in sig
    assert "<path>" in sig


def test_iso_timestamp_stripped() -> None:
    sig = normalise("failed at 2024-01-15T12:34:56Z with exit 1")
    assert "2024" not in sig
    assert "<ts>" in sig
    assert "1" in sig


def test_uuid_stripped() -> None:
    sig = normalise("session 550e8400-e29b-41d4-a716-446655440000 expired")
    assert "550e8400" not in sig
    assert "<uuid>" in sig


def test_line_col_span_stripped() -> None:
    sig = normalise("error: bad syntax")
    assert "<path>" in sig or "bad syntax" in sig
    # More specifically: <path>:42:10 → <path>
    sig2 = normalise("/foo/bar.py:42:10: error: bad syntax")
    assert ":42:10" not in sig2
    assert "bad syntax" in sig2


def test_standalone_number_preserved() -> None:
    sig = normalise("process exited with code 42")
    assert "42" in sig


def test_standalone_number_preserved_exit_1() -> None:
    sig = normalise("subprocess returned exit code 1")
    assert "1" in sig


def test_error_count_preserved() -> None:
    sig = normalise("found 3 errors in your code")
    assert "3" in sig


def test_excess_whitespace_collapsed() -> None:
    sig = normalise("error:   too   many\n  spaces\t here")
    assert "  " not in sig
    assert "\n" not in sig
    assert "\t" not in sig


def test_leading_trailing_whitespace_stripped() -> None:
    sig = normalise("  error message  ")
    assert not sig.startswith(" ")
    assert not sig.endswith(" ")


def test_deterministic_same_input_same_output() -> None:
    text = "error: /home/user/file.py:42:10: TypeError: bad value at 2024-03-01T00:00:00Z"
    assert normalise(text) == normalise(text)


def test_same_logical_error_different_paths_same_signature() -> None:
    a = normalise("error in /home/alice/project/src/foo.py: something failed")
    b = normalise("error in /home/bob/work/src/foo.py: something failed")
    assert a == b


def test_same_logical_error_different_line_numbers_same_signature() -> None:
    a = normalise("TypeError at /proj/foo.py:10:5: bad type")
    b = normalise("TypeError at /proj/foo.py:99:1: bad type")
    assert a == b


def test_different_errors_produce_different_signatures() -> None:
    a = normalise("TypeError: expected string got int")
    b = normalise("ValueError: out of range")
    assert a != b


def test_different_exit_codes_produce_different_signatures() -> None:
    a = normalise("process exited with code 1")
    b = normalise("process exited with code 2")
    assert a != b


def test_output_bounded_by_max_len() -> None:
    long_text = "x" * (MAX_LEN * 2)
    assert len(normalise(long_text)) <= MAX_LEN


def test_empty_string_normalises_to_empty() -> None:
    assert normalise("") == ""


def test_bare_relative_path_stripped() -> None:
    sig = normalise("error in src/foo.py:10: bad value")
    assert "src/foo.py" not in sig
    assert "<path>" in sig


def test_bare_relative_multi_segment_stripped() -> None:
    sig = normalise("cannot open a/b/c.py")
    assert "a/b/c.py" not in sig
    assert "<path>" in sig


def test_same_logical_error_different_relative_paths_same_signature() -> None:
    a = normalise("error in src/foo.py:10: something failed")
    b = normalise("error in lib/foo.py:20: something failed")
    assert a == b


def test_combined_volatile_stripped_stable_preserved() -> None:
    text = (
        "RuntimeError: /home/ci/build/src/main.py:123:4: "
        "uuid 550e8400-e29b-41d4-a716-446655440000 at 2025-06-01T10:00:00Z exit 1"
    )
    sig = normalise(text)
    assert "/home" not in sig
    assert "550e8400" not in sig
    assert "2025" not in sig
    assert ":123:4" not in sig
    assert "RuntimeError" in sig
    assert "1" in sig


# --- Explicit span-stripping tests (R2) ---


def test_bare_filename_colon_lc_span_stripped() -> None:
    sig = normalise("error in foo.py:42:10: bad value")
    assert ":42" not in sig
    assert ":10" not in sig
    assert "<path>" in sig


def test_bare_filename_colon_l_span_stripped() -> None:
    sig = normalise("error in foo.py:42: bad value")
    assert ":42" not in sig
    assert "<path>" in sig


def test_bare_filename_line_form_stripped() -> None:
    sig = normalise("error in foo.py line 42: bad value")
    assert "line 42" not in sig
    assert "<path>" in sig


def test_bare_filename_paren_lc_stripped() -> None:
    sig = normalise("error in foo.py (42,10): bad value")
    assert "(42,10)" not in sig
    assert "<path>" in sig


def test_same_span_forms_same_signature() -> None:
    a = normalise("error in foo.py:42:10: bad value")
    b = normalise("error in foo.py line 42: bad value")
    c = normalise("error in foo.py (42,10): bad value")
    assert a == b == c


def test_paren_shape_tuple_preserved() -> None:
    # Shape tuples like (2, 3) without a preceding path must not be stripped.
    sig = normalise("ValueError: expected shape (2, 3)")
    assert "(2, 3)" in sig


def test_paren_different_shape_tuples_distinct() -> None:
    a = normalise("ValueError: expected shape (2, 3)")
    b = normalise("ValueError: expected shape (4, 5)")
    assert a != b


def test_bracket_lc_after_path_stripped() -> None:
    sig = normalise("error in foo.py [42:10] bad value")
    assert "[42:10]" not in sig
    assert "<path>" in sig


def test_bracket_lc_standalone_preserved() -> None:
    # [N:N] without a preceding path is a slice literal, not a position — preserve it.
    sig = normalise("IndexError: slice [1:2] invalid")
    assert "[1:2]" in sig


def test_bracket_lc_different_slices_distinct() -> None:
    a = normalise("IndexError: slice [1:2] invalid")
    b = normalise("IndexError: slice [3:4] invalid")
    assert a != b


def test_exit_code_key_value_preserved() -> None:
    sig = normalise("exit_code:1 something failed")
    assert "1" in sig
    assert "exit_code" in sig


def test_exit_code_key_value_distinct() -> None:
    a = normalise("exit_code:1 something failed")
    b = normalise("exit_code:2 something failed")
    assert a != b


def test_status_key_value_preserved() -> None:
    sig = normalise("status:2 operation failed")
    assert "2" in sig

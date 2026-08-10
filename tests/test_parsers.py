"""Test the human-typed quantity parsers (`qbit_core.shared.parsers`).

These values reach destructive commands, so the tests focus on the
forms a shell user actually types and on every way a wrong reading
would be silent: unit families that differ by 4.8%, abbreviations that
belong to neither, calendar units with no fixed length, and the bare
number that could be read as both a fraction and a percentage.
"""

import pytest

from qbit_core.errors import InvalidInputError
from qbit_core.shared.parsers import (
    parse_duration,
    parse_percentage,
    parse_ratio,
    parse_size,
)

# --- sizes ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0", 0),
        ("1024", 1024),
        ("512B", 512),
        ("1KiB", 1024),
        ("512KiB", 524_288),
        ("10GiB", 10_737_418_240),
        ("1.5TiB", 1_649_267_441_664),
        ("1PiB", 1024**5),
        ("1KB", 1000),
        ("500MB", 500_000_000),
        ("2GB", 2_000_000_000),
        ("1TB", 1_000_000_000_000),
        ("1PB", 1000**5),
    ],
)
def test_parse_size_accepts_both_unit_families(value: str, expected: int):
    assert parse_size(value) == expected


def test_binary_and_decimal_units_are_never_conflated() -> None:
    """The 4.8% gap between 500MB and 500MiB is invisible when read back
    from a summary, and destructive on a delete -- the suffix alone
    decides, never a default."""
    assert parse_size("500MB") == 500_000_000
    assert parse_size("500MiB") == 524_288_000
    assert parse_size("500MB") != parse_size("500MiB")


@pytest.mark.parametrize("value", ["500mib", "500MIB", "500MiB", "500mIb"])
def test_parse_size_suffix_is_case_insensitive(value: str) -> None:
    assert parse_size(value) == 524_288_000


def test_case_never_decides_the_unit_family() -> None:
    """Only the `i` separates binary from decimal; lowering the case of
    a decimal suffix must not turn it into a binary one."""
    assert parse_size("500mb") == parse_size("500MB")
    assert parse_size("500mib") == parse_size("500MiB")


@pytest.mark.parametrize("value", ["500M", "10G", "1T", "5K", "2P"])
def test_parse_size_rejects_ambiguous_abbreviations(value: str) -> None:
    with pytest.raises(InvalidInputError, match="Ambiguous size unit"):
        parse_size(value)


def test_ambiguous_abbreviation_error_names_both_alternatives() -> None:
    with pytest.raises(InvalidInputError) as error:
        parse_size("500M")

    message = str(error.value)
    assert "500MiB" in message
    assert "500MB" in message


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "MB",
        "500 MB",
        "1 024",
        "-5",
        "-1GiB",
        "abc",
        "5Gib?",
        "1,5GiB",
    ],
)
def test_parse_size_rejects_malformed_values(value: str) -> None:
    with pytest.raises(InvalidInputError):
        parse_size(value)


def test_parse_size_rejects_an_unknown_unit() -> None:
    with pytest.raises(InvalidInputError, match="Unknown size unit"):
        parse_size("10ZiB")


# --- durations --------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0s", 0),
        ("30s", 30),
        ("30m", 1_800),
        ("12h", 43_200),
        ("7d", 604_800),
        ("90d", 7_776_000),
        ("2w", 1_209_600),
        ("1.5h", 5_400),
        ("365d", 31_536_000),
    ],
)
def test_parse_duration_accepts_one_unit(value: str, expected: int) -> None:
    assert parse_duration(value) == expected


def test_parse_duration_suffix_is_case_insensitive() -> None:
    assert parse_duration("90D") == parse_duration("90d")


@pytest.mark.parametrize("value", ["1y", "2y", "1mo", "6months", "3mon"])
def test_parse_duration_rejects_calendar_units(value: str) -> None:
    """A month has no fixed length and a year has two plausible ones;
    `1y` would look precise while being an approximation."""
    with pytest.raises(InvalidInputError, match="Calendar unit"):
        parse_duration(value)


def test_calendar_unit_error_points_at_the_explicit_form() -> None:
    with pytest.raises(InvalidInputError) as error:
        parse_duration("1y")

    assert "365d" in str(error.value)


@pytest.mark.parametrize("value", ["1d12h", "1h30m", "90"])
def test_parse_duration_rejects_compound_and_unitless_values(
    value: str,
) -> None:
    with pytest.raises(InvalidInputError):
        parse_duration(value)


@pytest.mark.parametrize("value", ["", "  ", "-7d", "d", "7 d", "7x"])
def test_parse_duration_rejects_malformed_values(value: str) -> None:
    with pytest.raises(InvalidInputError):
        parse_duration(value)


# --- percentages ------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0", 0.0),
        ("1", 1.0),
        ("0.95", 0.95),
        ("0%", 0.0),
        ("95%", 0.95),
        ("99.5%", 0.995),
        ("100%", 1.0),
    ],
)
def test_parse_percentage_accepts_both_forms(
    value: str, expected: float
) -> None:
    assert parse_percentage(value) == pytest.approx(expected)


def test_bare_one_is_full_progress_not_one_percent() -> None:
    """A bare number is already a fraction, so `1` is 100%."""
    assert parse_percentage("1") == 1.0
    assert parse_percentage("1%") == pytest.approx(0.01)


@pytest.mark.parametrize("value", ["95", "1.5", "-0.5", "2"])
def test_parse_percentage_rejects_ambiguous_bare_numbers(value: str) -> None:
    """`95` could be read as 95% or as 9500%; refusing it is what makes
    the two accepted forms unambiguous."""
    with pytest.raises(InvalidInputError, match="fraction range"):
        parse_percentage(value)


def test_out_of_range_bare_number_suggests_the_percent_form() -> None:
    with pytest.raises(InvalidInputError) as error:
        parse_percentage("95")

    assert "95%" in str(error.value)


@pytest.mark.parametrize("value", ["101%", "-1%", "-5%"])
def test_parse_percentage_rejects_out_of_range_percentages(
    value: str,
) -> None:
    with pytest.raises(InvalidInputError, match="out of range"):
        parse_percentage(value)


@pytest.mark.parametrize("value", ["", " ", "%", "abc%", "nan", "inf"])
def test_parse_percentage_rejects_malformed_values(value: str) -> None:
    with pytest.raises(InvalidInputError):
        parse_percentage(value)


# --- ratios -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [("0", 0.0), ("1", 1.0), ("2.5", 2.5), ("500", 500.0)],
)
def test_parse_ratio_accepts_any_finite_non_negative_value(
    value: str, expected: float
) -> None:
    assert parse_ratio(value) == pytest.approx(expected)


def test_parse_ratio_has_no_upper_bound() -> None:
    """A long-lived seed legitimately reaches a ratio in the hundreds."""
    assert parse_ratio("9999.5") == pytest.approx(9999.5)


@pytest.mark.parametrize("value", ["-1", "-0.5"])
def test_parse_ratio_rejects_negative_values(value: str) -> None:
    with pytest.raises(InvalidInputError, match="must not be negative"):
        parse_ratio(value)


@pytest.mark.parametrize("value", ["nan", "NaN", "inf", "-inf", "Infinity"])
def test_parse_ratio_rejects_non_finite_values(value: str) -> None:
    """`float()` accepts all of these, and every one would silently
    break a bounds comparison."""
    with pytest.raises(InvalidInputError, match="finite"):
        parse_ratio(value)


@pytest.mark.parametrize("value", ["", "   ", "abc", "1 5"])
def test_parse_ratio_rejects_malformed_values(value: str) -> None:
    with pytest.raises(InvalidInputError):
        parse_ratio(value)


# --- shared guarantees ------------------------------------------------------


def test_every_parser_raises_the_same_local_input_error() -> None:
    """One exception type across the family, so a CLI boundary catches
    `InvalidInputError` once instead of per parser."""
    for parser in (parse_size, parse_duration, parse_percentage, parse_ratio):
        with pytest.raises(InvalidInputError):
            parser("definitely not a value")

"""Parse human-typed quantities into domain values.

Bounded on purpose: sizes, durations, percentages and ratios -- the
value families a selector, a retention rule or a reporting threshold
accepts from an operator. Not a general-purpose parsing module.

Every function is pure and raises `InvalidInputError` before any
qBittorrent call, so an unparsable value can never reach a mutation.
Error messages name the accepted forms rather than restating the
rejected input, because these are typed by a human at a shell prompt.
"""

import math
import re

from qbit_core.errors import InvalidInputError

__all__ = [
    "parse_duration",
    "parse_percentage",
    "parse_rate",
    "parse_ratio",
    "parse_size",
]

# Suffix -> multiplier. Binary (IEC) and decimal (SI) are both accepted
# and never conflated: `MiB` is 1024**2, `MB` is 1000**2. Comparison is
# case-insensitive, so what distinguishes the two families is the `i`,
# never the capitalization -- `500mib` and `500MiB` mean the same thing.
_SIZE_UNITS: dict[str, int] = {
    "b": 1,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
    "pib": 1024**5,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "pb": 1000**5,
}

# Abbreviations a user may reach for that belong to neither family.
# Rejected rather than guessed: `500M` is 500 000 000 to some tools and
# 524 288 000 to others, and the difference is destructive on a delete.
_AMBIGUOUS_SIZE_UNITS = frozenset({"k", "m", "g", "t", "p"})

_SIZE_PATTERN = re.compile(r"^(?P<number>[0-9]+(?:\.[0-9]+)?)(?P<unit>[a-z]*)$")

_DURATION_UNITS: dict[str, int] = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
    "w": 604800,
}

# Calendar units are deliberately absent: a month has no fixed length
# and a year has two plausible ones, so `1y` would look precise while
# being an approximation. `365d` says exactly what it means.
_CALENDAR_DURATION_UNITS = frozenset({"y", "mo", "mon", "month", "months"})

_DURATION_PATTERN = re.compile(
    r"^(?P<number>[0-9]+(?:\.[0-9]+)?)(?P<unit>[a-z]+)$"
)

_SIZE_UNIT_HELP = (
    "binary KiB/MiB/GiB/TiB/PiB (1024) or decimal KB/MB/GB/TB/PB (1000)"
)


def parse_size(value: str) -> int:
    """Parse a byte size such as `1024`, `500MB` or `1.5TiB`.

    A bare number is bytes. The unit alone decides the multiplier;
    single-letter abbreviations (`500M`) are rejected as ambiguous
    rather than resolved to either family.
    """
    normalized = _require_compact(value, field_name="size")
    match = _SIZE_PATTERN.match(normalized.lower())
    if match is None:
        raise InvalidInputError(
            f"Invalid size '{value}'. Use a number optionally followed by "
            f"{_SIZE_UNIT_HELP}, e.g. '1024', '500MB' or '1.5TiB'."
        )

    unit = match.group("unit")
    if unit == "":
        multiplier = 1
    elif unit in _AMBIGUOUS_SIZE_UNITS:
        raise InvalidInputError(
            f"Ambiguous size unit '{unit.upper()}' in '{value}'. Spell it "
            f"out: '{match.group('number')}{unit.upper()}iB' for binary "
            f"(1024) or '{match.group('number')}{unit.upper()}B' for "
            "decimal (1000)."
        )
    elif unit in _SIZE_UNITS:
        multiplier = _SIZE_UNITS[unit]
    else:
        raise InvalidInputError(
            f"Unknown size unit '{unit}' in '{value}'. Supported units: "
            f"{_SIZE_UNIT_HELP}."
        )

    return round(float(match.group("number")) * multiplier)


# qBittorrent encodes "no limit" as a zero rate. qbit-ops accepts the
# word instead, and refuses the digit: `0` reads as "zero bytes" to a
# human, which is the opposite of what it does.
UNLIMITED_RATE = 0

_UNLIMITED_KEYWORD = "unlimited"

# A rate is per second by definition here, so the suffix is decoration a
# human may or may not type. Both forms mean the same thing.
_RATE_SUFFIXES = ("/s", "ps")


def parse_rate(value: str) -> int:
    """Parse a transfer rate in bytes per second, or `unlimited`.

    A unit is mandatory, unlike `parse_size`: a bare `500` on a rate
    would be 500 bytes per second where the operator meant 500 KB/s, a
    thousandfold error with no signal. The trailing `/s` (or `ps`) is
    accepted and ignored -- a rate is per second either way.

    `unlimited` returns `UNLIMITED_RATE`; the literal `0` is refused, so
    "no limit" always has exactly one spelling.
    """
    # Case is folded for the keyword and suffix comparisons only. What
    # reaches `parse_size` keeps the case that was typed, so its own
    # messages can quote `500M` back rather than a `500m` nobody wrote.
    normalized = _require_compact(value, field_name="rate")
    if normalized.lower() == _UNLIMITED_KEYWORD:
        return UNLIMITED_RATE

    for suffix in _RATE_SUFFIXES:
        if normalized.lower().endswith(suffix) and len(normalized) > len(
            suffix
        ):
            normalized = normalized[: -len(suffix)]
            break

    match = _SIZE_PATTERN.match(normalized.lower())
    if match is not None:
        # Zero is checked before the missing unit, or a bare `0` would be
        # answered with "did you mean '0KB'?" -- a suggestion that is
        # itself refused two lines later.
        if float(match.group("number")) == 0.0:
            raise _zero_rate_error(value)
        if match.group("unit") == "":
            number = match.group("number")
            raise InvalidInputError(
                f"Rate '{value}' needs a unit. Did you mean '{number}KB'? "
                f"Use {_SIZE_UNIT_HELP}, or '{_UNLIMITED_KEYWORD}' to "
                "remove the limit."
            )

    rate = parse_size(normalized)
    if rate == UNLIMITED_RATE:
        # `parse_size` rounds, so a non-zero `0.4b` still lands on zero.
        # Refused here rather than passed through: it would otherwise be
        # a silent second spelling of "no limit".
        raise _zero_rate_error(value)
    return rate


def _zero_rate_error(value: str) -> InvalidInputError:
    return InvalidInputError(
        f"Rate '{value}' is zero, which qBittorrent reads as 'no limit'. "
        f"Write '{_UNLIMITED_KEYWORD}' when that is what you mean."
    )


def parse_duration(value: str) -> int:
    """Parse a duration such as `30m`, `12h` or `90d`, in seconds.

    Exactly one unit per value: `1d12h` is rejected, since a compound
    form invites silently dropping a component. Calendar units are
    rejected too -- see `_CALENDAR_DURATION_UNITS`.
    """
    normalized = _require_compact(value, field_name="duration")
    match = _DURATION_PATTERN.match(normalized.lower())
    if match is None:
        raise InvalidInputError(
            f"Invalid duration '{value}'. Use a number followed by one of "
            "s, m, h, d, w -- e.g. '30m', '12h' or '90d'."
        )

    unit = match.group("unit")
    if unit in _CALENDAR_DURATION_UNITS:
        raise InvalidInputError(
            f"Calendar unit '{unit}' is not supported in '{value}': months "
            "and years have no fixed length. Use days instead, e.g. '365d'."
        )
    if unit not in _DURATION_UNITS:
        raise InvalidInputError(
            f"Unknown duration unit '{unit}' in '{value}'. Supported units: "
            "s, m, h, d, w."
        )

    return round(float(match.group("number")) * _DURATION_UNITS[unit])


def parse_percentage(value: str) -> float:
    """Parse a progress value as a fraction in `[0, 1]`.

    Two accepted forms, kept unambiguous by the suffix: `95%` is a
    percentage, a bare number is already a fraction. A bare number
    outside `[0, 1]` is rejected rather than guessed -- `95` would
    otherwise read as both 95% and 9500%.
    """
    normalized = _require_compact(value, field_name="percentage")

    if normalized.endswith("%"):
        percent = _parse_finite_float(normalized[:-1], original=value)
        if not 0.0 <= percent <= 100.0:
            raise InvalidInputError(
                f"Percentage '{value}' is out of range. Use a value between "
                "0% and 100%."
            )
        return percent / 100.0

    fraction = _parse_finite_float(normalized, original=value)
    if not 0.0 <= fraction <= 1.0:
        raise InvalidInputError(
            f"Bare value '{value}' is out of the 0-1 fraction range. Write "
            f"'{normalized}%' for a percentage, or a fraction like '0.95'."
        )
    return fraction


def parse_ratio(value: str) -> float:
    """Parse a share ratio: any finite value `>= 0`, with no upper bound.

    Deliberately unbounded above -- a long-lived seed legitimately
    reaches a ratio in the hundreds.
    """
    normalized = _require_compact(value, field_name="ratio")
    ratio = _parse_finite_float(normalized, original=value)
    if ratio < 0.0:
        raise InvalidInputError(f"Ratio '{value}' must not be negative.")
    return ratio


def _require_compact(value: str, *, field_name: str) -> str:
    """Return `value` stripped, rejecting blanks and internal whitespace.

    Internal whitespace is rejected rather than collapsed: `1 000` and
    `500 MB` are typos worth surfacing, not shorthand worth accepting.
    """
    stripped = value.strip()
    if stripped == "":
        raise InvalidInputError(
            f"A {field_name} value must not be empty or whitespace-only."
        )
    if any(character.isspace() for character in stripped):
        raise InvalidInputError(
            f"Invalid {field_name} '{value}': remove the spaces inside the "
            "value."
        )
    return stripped


def _parse_finite_float(candidate: str, *, original: str) -> float:
    """Parse a float, rejecting NaN and infinities.

    `float()` happily accepts `nan`, `inf` and `-inf`; every one of them
    would silently break a bounds comparison, so they are refused here
    rather than at each call site.
    """
    try:
        parsed = float(candidate)
    except (TypeError, ValueError):
        raise InvalidInputError(f"Invalid number '{original}'.") from None

    if math.isnan(parsed) or math.isinf(parsed):
        raise InvalidInputError(f"'{original}' is not a finite number.")
    return parsed

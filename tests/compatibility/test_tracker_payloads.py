"""Compatibility contract tests: tracker payload fixtures.

Exercises `qbit_ops.qbit.fields` (raw status/URL access) and
`qbit_ops.features.trackers` (health classification, sanitization) -- the exact
production boundary the CLI and TUI use.
"""

from __future__ import annotations

import enum

from qbit_ops.features.trackers import (
    TrackerHealth,
    classify_raw_tracker_status,
    sanitize_tracker_text,
)
from qbit_ops.qbit.fields import (
    get_field_as_string,
    get_raw_tracker_status,
    is_disabled_tracker,
    is_disabled_tracker_status,
)
from tests.compatibility._fixture_loader import load_fixture


def test_working_tracker_classifies_healthy() -> None:
    fixture = load_fixture("trackers", "working")
    raw_status = get_raw_tracker_status(fixture.payload)

    health, enabled = classify_raw_tracker_status(raw_status)

    assert health is TrackerHealth.HEALTHY
    assert enabled is True
    assert is_disabled_tracker(fixture.payload) is False


def test_updating_tracker_classifies_healthy_not_warning() -> None:
    """UPDATING (mid-announce) is treated as HEALTHY, not WARNING --
    see app/trackers.py's rationale comment."""
    fixture = load_fixture("trackers", "updating")
    raw_status = get_raw_tracker_status(fixture.payload)

    health, enabled = classify_raw_tracker_status(raw_status)

    assert health is TrackerHealth.HEALTHY
    assert enabled is True


def test_error_tracker_classifies_critical_with_sanitized_message() -> None:
    fixture = load_fixture("trackers", "error")
    raw_status = get_raw_tracker_status(fixture.payload)

    health, enabled = classify_raw_tracker_status(raw_status)

    assert health is TrackerHealth.CRITICAL
    # CRITICAL is still an *active* (non-disabled) endpoint -- `enabled`
    # is only False for the DISABLED code itself.
    assert enabled is True
    message = get_field_as_string(fixture.payload, "msg")
    assert sanitize_tracker_text(message) == message  # no URL to strip here


def test_disabled_dht_pseudo_tracker_is_disabled() -> None:
    fixture = load_fixture("trackers", "disabled_pseudo_tracker_dht")

    assert is_disabled_tracker(fixture.payload) is True


def test_disabled_real_tracker_with_int_status_is_disabled() -> None:
    fixture = load_fixture("trackers", "disabled_real_tracker_int_status")

    assert is_disabled_tracker(fixture.payload) is True
    assert isinstance(fixture.payload["status"], int)


def test_enum_like_disabled_status_is_still_classified_disabled() -> None:
    """Mirrors qbittorrentapi.definitions.TrackerStatus's real
    `(int, Enum)` bases, not `enum.IntEnum` -- an `IntEnum`-shaped
    status must still classify disabled, not active."""

    class _TrackerStatusLike(int, enum.Enum):
        DISABLED = 0

    assert str(_TrackerStatusLike.DISABLED) != "0"  # not "0" for this base
    assert is_disabled_tracker_status(_TrackerStatusLike.DISABLED) is True

    tracker = {
        "url": "https://tracker.example/announce",
        "status": _TrackerStatusLike.DISABLED,
    }
    assert is_disabled_tracker(tracker) is True


def test_missing_optional_message_defaults_to_empty_string() -> None:
    fixture = load_fixture("trackers", "missing_optional_message")

    assert get_field_as_string(fixture.payload, "msg") == ""


def test_message_with_secret_like_material_is_sanitized() -> None:
    """A tracker error message echoing a full announce URL (with a fake
    placeholder passkey) must have the URL entirely stripped by
    sanitize_tracker_text() before it could ever reach output."""
    fixture = load_fixture("trackers", "message_with_secret_like_material")
    message = get_field_as_string(fixture.payload, "msg")

    sanitized = sanitize_tracker_text(message)

    assert "FAKE_PLACEHOLDER_PASSKEY" not in sanitized
    assert "tracker.example" not in sanitized
    assert "<redacted-url>" in sanitized


def test_extra_future_field_is_silently_ignored() -> None:
    fixture = load_fixture("trackers", "extra_future_field")
    raw_status = get_raw_tracker_status(fixture.payload)

    health, _enabled = classify_raw_tracker_status(raw_status)

    assert health is TrackerHealth.HEALTHY
    assert "num_peers" in fixture.payload


def test_unknown_status_code_classifies_unknown_not_guessed() -> None:
    fixture = load_fixture("trackers", "unknown_status")
    raw_status = get_raw_tracker_status(fixture.payload)

    health, enabled = classify_raw_tracker_status(raw_status)

    assert health is TrackerHealth.UNKNOWN
    assert enabled is None
    assert is_disabled_tracker(fixture.payload) is False

"""Shared error categories and structured domain exceptions.

Kept free of Typer and Rich so this module stays independently
reusable by any future interface (CLI, TUI) without pulling in
presentation concerns — see docs/PHILOSOPHY.md and
docs/ERRORS_AND_EXIT_CODES.md.

`ErrorCategory` is a semantic label, not a numeric exit code: several
categories can share a command's exit code, and the same category can
map to different codes across commands. See
docs/ERRORS_AND_EXIT_CODES.md for the shared principles and the exact
per-command tables.
"""

from dataclasses import dataclass
from enum import StrEnum


class ErrorCategory(StrEnum):
    """Classify why a command failed, independent of rendering or exit code."""

    INVALID_INPUT = "invalid_input"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"
    UNAVAILABLE = "unavailable"
    AUTHENTICATION = "authentication"
    CONFIGURATION = "configuration"
    CANCELLED = "cancelled"
    DOMAIN_FAILURE = "domain_failure"
    INTERNAL = "internal"


@dataclass(frozen=True)
class AppError:
    """Represent one handled failure, ready for a future TUI to consume.

    `code` is a short stable machine identifier (e.g.
    `"blank_hash"`, `"authentication_failed"`), distinct from the
    process exit code. `target` is a safe identity (a hash prefix, a
    normalized tracker host) — never a raw tracker URL or credential.
    """

    category: ErrorCategory
    code: str
    message: str
    remediation: str | None = None
    target: str | None = None


class InvalidInputError(ValueError):
    """Report a locally-invalid CLI invocation.

    Raised by local validation performed before configuration loading
    or any qBittorrent API call — a blank/whitespace `--hash` or
    `--tracker`, a contradictory filter combination, an unsupported
    `--format` — so a malformed identifier is never sent to
    qBittorrent and then relabelled as a generic remote failure.
    """


class QbitConnectionError(RuntimeError):
    """Report that qBittorrent could not be reached."""


class QbitAuthenticationError(RuntimeError):
    """Report that qBittorrent rejected the configured credentials."""


def require_non_blank(value: str, *, field_name: str) -> str:
    """Return `value` stripped, raising `InvalidInputError` if blank.

    The one shared validator for `--hash`/`--tracker`-style options:
    normalizes once so callers pass the stripped value on to domain
    functions instead of re-validating downstream.
    """
    normalized = value.strip()
    if normalized == "":
        raise InvalidInputError(
            f"{field_name} must not be empty or whitespace-only."
        )
    return normalized

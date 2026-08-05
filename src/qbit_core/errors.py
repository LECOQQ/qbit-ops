"""Shared error categories and structured domain exceptions.

`ErrorCategory` is a semantic label, not a numeric exit code: several
categories can share a command's exit code, and the same category can
map to different codes across commands. See docs/ERRORS_AND_EXIT_CODES.md.
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
    """Represent one handled failure.

    `code` is a short stable machine identifier, distinct from the
    process exit code. `target` is a safe identity (a hash prefix, a
    normalized tracker host) — never a raw tracker URL or credential.
    """

    category: ErrorCategory
    code: str
    message: str
    remediation: str | None = None
    target: str | None = None


class QbitCoreError(Exception):
    """Base class for every domain exception `qbit_core` raises."""


class InvalidInputError(QbitCoreError, ValueError):
    """Report a locally-invalid request, raised before any API call."""


class QbitConnectionError(QbitCoreError, RuntimeError):
    """Report that qBittorrent could not be reached."""


class QbitAuthenticationError(QbitCoreError, RuntimeError):
    """Report that qBittorrent rejected the configured credentials."""


def require_non_blank(value: str, *, field_name: str) -> str:
    """Return `value` stripped, raising `InvalidInputError` if blank."""
    normalized = value.strip()
    if normalized == "":
        raise InvalidInputError(
            f"{field_name} must not be empty or whitespace-only."
        )
    return normalized

"""CLI-level exception-to-exit-code mapping and the qBittorrent client/config
construction seam every command shares.

`create_qbit_client`/`load_qbit_config` are re-exported here as the single
seam every `cli/commands/*.py` module uses: call them as
`error_boundary.create_qbit_client()`, never via a local `from ... import`
copy, so a single `monkeypatch.setattr` affects every command uniformly.
"""

import functools
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, NoReturn

import typer

from qbit_core.errors import (
    AppError,
    ErrorCategory,
    QbitAuthenticationError,
    QbitConnectionError,
)
from qbit_core.qbit.client import is_qbit_error
from qbit_core.shared.selectors import AmbiguousTorrentHashError
from qbit_ops.app_services import create_qbit_client
from qbit_ops.cli.exit_codes import ExitCode, StatusExitCode
from qbit_ops.cli.rendering import print_ambiguous_hash_error, print_error
from qbit_ops.config import ConfigError, load_qbit_config

__all__ = [
    "create_qbit_client",
    "load_qbit_config",
    "catch_internal_errors",
    "qbit_error_boundary",
    "fail",
    "fail_status_usage",
    "fail_ambiguous_hash",
    "record_app_error",
    "last_app_error",
]


def catch_internal_errors(
    command: Callable[..., None],
) -> Callable[..., None]:
    """Report an unexpected exception as an internal error, not a remote one.

    `typer.Exit` and `KeyboardInterrupt` always pass through untouched;
    anything else is a genuine programming defect and is reported with a
    concise message (no `repr()`, no traceback, no secret-bearing values)
    and the shared `ExitCode.INTERNAL` (70), regardless of the wrapped
    command's own exit-code scheme.
    """

    @functools.wraps(command)
    def wrapper(*args: Any, **kwargs: Any) -> None:
        try:
            command(*args, **kwargs)
        except typer.Exit:
            raise
        except KeyboardInterrupt:
            raise
        except Exception as error:
            message = f"Internal error: {type(error).__name__}: {error}"
            record_app_error(
                AppError(
                    category=ErrorCategory.INTERNAL,
                    code="internal_error",
                    message=message,
                )
            )
            raise typer.Exit(code=ExitCode.INTERNAL) from error

    return wrapper


last_app_error: AppError | None = None
"""The most recently constructed `AppError`, for tests to assert on
structured fields instead of parsing rendered stderr text."""


def record_app_error(app_error: AppError, *, render: bool = True) -> None:
    """Record `app_error` as the last handled failure.

    Prints `app_error.message` to stderr via `print_error()` unless
    `render` is `False` (used by `fail_ambiguous_hash()`, which renders
    the candidate list itself instead of a plain message line).
    """
    global last_app_error
    last_app_error = app_error
    if render:
        print_error(app_error.message)


def fail(
    message: str,
    category: ErrorCategory = ErrorCategory.DOMAIN_FAILURE,
    *,
    code: str | None = None,
    target: str | None = None,
) -> NoReturn:
    """Build an `AppError`, print its message, and exit with a failure code.

    `code` defaults to `category.value` when omitted. `target` is a
    safe identity (a hash prefix, a normalized tracker host) — never a
    raw tracker URL or credential.
    """
    record_app_error(
        AppError(
            category=category,
            code=code or category.value,
            message=message,
            target=target,
        )
    )
    raise typer.Exit(code=ExitCode.ERROR)


def fail_status_usage(
    message: str,
    category: ErrorCategory = ErrorCategory.INVALID_INPUT,
) -> NoReturn:
    """Build an `AppError` and exit with `status`'s invalid-usage code.

    `category` defaults to `INVALID_INPUT` (a rejected option
    combination); pass `CONFIGURATION` for a `ConfigError`-driven failure.
    """
    record_app_error(
        AppError(category=category, code=category.value, message=message)
    )
    raise typer.Exit(code=StatusExitCode.INVALID_USAGE)


def fail_ambiguous_hash(error: AmbiguousTorrentHashError) -> NoReturn:
    """Print ambiguous hash candidates and exit with a failure code.

    Deliberately reuses `ExitCode.ERROR` rather than introducing a new
    ambiguity-specific exit code.
    """
    record_app_error(
        AppError(
            category=ErrorCategory.AMBIGUOUS,
            code="ambiguous_hash",
            message=f"Hash prefix '{error.value}' matches multiple torrents.",
            remediation="Use a longer prefix.",
            target=error.value,
        ),
        render=False,
    )
    print_ambiguous_hash_error(error.value, error.candidates)
    raise typer.Exit(code=ExitCode.ERROR)


@contextmanager
def qbit_error_boundary() -> Iterator[None]:
    """Convert expected qBittorrent/config failures into a rendered CLI error.

    Reports known failure modes (invalid configuration, authentication
    rejection, connection/API unavailability) through `fail()`. Anything
    else is left to propagate to `catch_internal_errors` as a genuine
    programming bug. `except Exception` is narrowed by `is_qbit_error()`,
    not widened by it. Keep the `with` block scoped to exactly the
    client-creation-plus-domain-call span: `typer.Exit` is a
    `RuntimeError` subclass, so widening it risks swallowing an `Exit`
    raised by rendering code further down the same command.
    """
    try:
        yield
    except typer.Exit:
        raise
    except ConfigError as error:
        fail(f"Configuration error: {error}", ErrorCategory.CONFIGURATION)
    except QbitAuthenticationError as error:
        fail(str(error), ErrorCategory.AUTHENTICATION)
    except QbitConnectionError as error:
        fail(str(error), ErrorCategory.UNAVAILABLE)
    except OSError as error:
        fail(f"qBittorrent API error: {error}", ErrorCategory.UNAVAILABLE)
    except Exception as error:
        if not is_qbit_error(error):
            raise
        fail(f"qBittorrent API error: {error}", ErrorCategory.UNAVAILABLE)

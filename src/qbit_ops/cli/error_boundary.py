"""Own CLI-level exception-to-exit-code behavior, and the qBittorrent
client/config construction seam every command shares.

Moved verbatim from `qbit_ops.main`: exit codes, stderr/stdout
separation, secret sanitization (via `qbit_ops.cli.rendering.print_error`),
JSON error behavior (an error never emits partial JSON to stdout -- see
`qbit_error_boundary`), and the treatment of unexpected internal errors
are all unchanged.

`create_qbit_client` and `load_qbit_config` are re-exported here (from
`qbit_ops.app_services` and `qbit_ops.config` respectively) as the
single canonical seam every `cli/commands/*.py` module uses to obtain a
client/config -- never a local `from ... import` copy. Every command
module must call `error_boundary.create_qbit_client()` /
`error_boundary.load_qbit_config()` as a module attribute access so a
single `monkeypatch.setattr("qbit_ops.cli.error_boundary.create_qbit_client",
...)` affects every command group uniformly, exactly like the single
shared `qbit_ops.main` module did before this split.
"""

import functools
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, NoReturn

import typer

from qbit_ops.app_services import create_qbit_client
from qbit_ops.cli.exit_codes import ExitCode, StatusExitCode
from qbit_ops.cli.rendering import print_ambiguous_hash_error, print_error
from qbit_ops.config import ConfigError, load_qbit_config
from qbit_ops.errors import (
    AppError,
    ErrorCategory,
    QbitAuthenticationError,
    QbitConnectionError,
)
from qbit_ops.qbit.client import is_qbit_error
from qbit_ops.shared.selectors import AmbiguousTorrentHashError

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

    The outermost boundary on every command: `typer.Exit` (Typer/Click's
    own control-flow exception, itself a `RuntimeError` subclass) always
    passes through untouched, as does `KeyboardInterrupt`. Anything else
    reaching this point escaped `qbit_error_boundary()` or arose outside
    it — a genuine programming defect — and is reported with a concise
    message (no `repr()`, no traceback, no secret-bearing values) and
    the shared `ExitCode.INTERNAL` (70), regardless of the wrapped
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
"""The most recently constructed `AppError`, for tests and future TUI reuse.

Set by every `fail*()` helper right before raising `typer.Exit`, so a
caller can assert on structured fields (`category`, `code`, `target`)
instead of parsing rendered stderr text — see
docs/ERRORS_AND_EXIT_CODES.md, "TUI-ready mapping". Not read by any
command itself; process-global state exists solely so the CLI keeps
rendering through plain stderr today while the same data is already
shaped for a future in-process consumer.
"""


def record_app_error(app_error: AppError, *, render: bool = True) -> None:
    """Record `app_error` as the last handled failure.

    Prints `app_error.message` to stderr via `print_error()` unless
    `render` is `False` — used by `fail_ambiguous_hash()`, which
    renders the candidate list itself instead of a plain message line.
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

    `status` (one-shot and `--watch`) uses `StatusExitCode`, not the
    shared `ExitCode`, for local CLI/configuration validation failures —
    see `StatusExitCode`'s docstring. `category` defaults to
    `INVALID_INPUT` (a rejected option combination); pass
    `CONFIGURATION` for a `ConfigError`-driven failure.
    """
    record_app_error(
        AppError(category=category, code=category.value, message=message)
    )
    raise typer.Exit(code=StatusExitCode.INVALID_USAGE)


def fail_ambiguous_hash(error: AmbiguousTorrentHashError) -> NoReturn:
    """Print ambiguous hash candidates and exit with a failure code.

    Deliberately reuses `ExitCode.ERROR` rather than introducing a new
    ambiguity-specific exit code: see docs/DECISIONS.md.
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

    Wraps client creation plus a domain call and reports the shared,
    expected failure modes (invalid local configuration, authentication
    rejection, connection/API unavailability) through `fail()` with
    the matching `ErrorCategory`. Anything else — a `TypeError`, an
    `AssertionError`, a bare `RuntimeError` from a genuine bug — is
    deliberately left to propagate to `catch_internal_errors` instead
    of being relabelled as a remote failure (see
    docs/ERRORS_AND_EXIT_CODES.md, "Internal error behavior").

    This module no longer imports `qbittorrentapi` directly (see
    `docs/audits/2026-07-package-refactor-plan.md` Phase 3 continuation):
    the generic CLI-level catch-all for a post-login `qbittorrentapi.APIError`
    uses the boundary-owned `qbit_ops.qbit.client.is_qbit_error()` predicate
    instead of naming the exception type here. `except Exception` below
    is deliberately narrowed by that predicate, not widened by it — any
    exception the predicate rejects is re-raised unchanged.

    Keep the `with` block scoped to exactly the client-creation-plus-
    domain-call span, never wider: widening it risks swallowing a
    `typer.Exit` raised by rendering code further down the same
    command (`typer.Exit` is a `RuntimeError` subclass, but is always
    re-raised untouched here first).
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

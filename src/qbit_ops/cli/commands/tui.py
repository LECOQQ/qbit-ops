"""Register the root-level `tui` command.

Textual is imported lazily inside the command body, so no other
command ever loads it.
"""

import math
from typing import Annotated

import typer

from qbit_core.errors import ErrorCategory
from qbit_ops.cli import error_boundary
from qbit_ops.cli.commands.status import DEFAULT_STATUS_WATCH_INTERVAL_SECONDS
from qbit_ops.config import (
    ConfigError,
    load_small_caps_titles_preference,
)


def register(app: typer.Typer) -> None:
    @app.command()
    @error_boundary.catch_internal_errors
    def tui(
        interval: Annotated[
            float,
            typer.Option(
                "--interval",
                help=(
                    "Seconds between refreshes (default: "
                    f"{DEFAULT_STATUS_WATCH_INTERVAL_SECONDS:g})."
                ),
            ),
        ] = DEFAULT_STATUS_WATCH_INTERVAL_SECONDS,
        small_caps_titles: Annotated[
            bool,
            typer.Option(
                "--small-caps-titles",
                help=(
                    "Draw window titles in Unicode small capitals "
                    "instead of letter-spaced capitals. Needs a font "
                    "covering all three blocks they live in; run "
                    "'qbit-ops doctor' to see which. Also settable as "
                    "QBIT_OPS_SMALL_CAPS_TITLES."
                ),
            ),
        ] = False,
    ) -> None:
        """Launch the interactive TUI.

        Shows the status header, torrent table, shared filters, search
        and focused-torrent details, and reaches exactly two kinds of
        write: the LOW-risk bulk actions of `QbitTorrentMutator`, and
        its own configuration file when qbit-ops is not set up yet.
        Requires the optional `tui` extra.
        """
        if not (interval > 0) or math.isinf(interval):
            error_boundary.fail(
                "--interval must be a finite, positive number of seconds.",
                ErrorCategory.INVALID_INPUT,
            )

        try:
            import textual  # noqa: F401
        except ModuleNotFoundError:
            # Two constraints shape this message, both found the hard way:
            #
            # - No literal URL. `fail()` -> `print_error()` runs every
            #   message through `sanitize_tracker_text()` unconditionally,
            #   which redacts URL-shaped text on sight, tracker or not.
            # - Square brackets are escaped (`\[`). Rich reads `[tui]` as
            #   markup and swallows it.
            #
            # Every install route is listed rather than guessed at: the
            # fix has a different *shape* per tool, and qbit-ops
            # deliberately does not detect how it was installed (see
            # .agents/features/version/SPEC.md, non-objectives).
            error_boundary.fail(
                "The TUI requires the optional 'tui' extra, which is not "
                "installed.\n\n"
                "Reinstall with the extra, using whichever tool you "
                "installed qbit-ops with:\n"
                r'  uv tool install "qbit-ops\[tui]"'
                "\n"
                r'  pipx install --force "qbit-ops\[tui]"'
                "\n\n"
                "or add Textual to the existing installation:\n"
                "  uv tool install --with textual qbit-ops\n"
                "  pipx inject qbit-ops textual\n\n"
                "From a repository checkout, for local development:\n"
                "  poetry install --extras tui",
                ErrorCategory.CONFIGURATION,
            )

        from qbit_ops.tui.app import run_tui

        host: str | None = None
        needs_setup = False
        try:
            host = error_boundary.load_qbit_config().host
        except ConfigError:
            # Absent *or* incomplete: the same form answers both, and
            # `qbit-ops tui` must never send someone to another command
            # to get started.
            needs_setup = True

        run_tui(
            client_factory=error_boundary.create_qbit_client,
            host=host,
            refresh_interval=interval,
            needs_setup=needs_setup,
            # The flag only ever turns them *on*: a configured
            # preference is an operator's standing answer, and a bare
            # `qbit-ops tui` must not silently overrule it.
            small_caps_titles=(
                small_caps_titles or load_small_caps_titles_preference()
            ),
        )

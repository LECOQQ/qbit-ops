"""Register the root-level `tui` command.

Moved from `qbit_ops.main`. No behavior change: Textual is still
imported lazily inside the command body, so no other command (and
`import qbit_ops.cli.app`/`qbit_ops.cli.commands.tui` themselves) ever
loads it -- see `tests/test_package_entrypoint.py` and
`tests/test_tui_cli.py`.
"""

import math
from typing import Annotated

import typer

from qbit_ops.cli import error_boundary
from qbit_ops.cli.commands.status import DEFAULT_STATUS_WATCH_INTERVAL_SECONDS
from qbit_ops.config import ConfigError
from qbit_ops.errors import ErrorCategory


def register(app: typer.Typer) -> None:
    """Register the `tui` command on the root Typer app."""

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
    ) -> None:
        """Launch the read-only interactive TUI.

        Read-only: status header, torrent table, shared filters, read-only
        search, and safe focused-torrent details -- no mutation is
        reachable. Requires the optional `tui` extra, installed from a
        repository checkout (`qbit-ops` is not published on PyPI --
        `pipx install "qbit-ops\\[tui]"` alone will always fail); Textual is
        imported lazily here so no other command ever loads it. See
        docs/COMMANDS.md ("TUI") for controls and
        docs/TUI_ARCHITECTURE_REVIEW.md for the architecture.
        """
        if not (interval > 0) or math.isinf(interval):
            error_boundary.fail(
                "--interval must be a finite, positive number of seconds.",
                ErrorCategory.INVALID_INPUT,
            )

        try:
            import textual  # noqa: F401
        except ModuleNotFoundError:
            # Deliberately no literal URL here: `fail()` -> `print_error()`
            # runs every message through `sanitize_tracker_text()`
            # unconditionally (see docs/PHILOSOPHY.md §15), which redacts
            # any URL-shaped text on sight, tracker or not -- a real
            # `https://github.com/...` clone URL here was silently replaced
            # with "<redacted-url>" in practice (found via the packaging
            # verification in docs/DECISIONS.md, worker-hardening phase),
            # defeating the remediation. Point at the README instead, which
            # is never sanitized.
            error_boundary.fail(
                "The TUI requires the optional 'tui' extra, which is not "
                "installed.\n\n"
                'qbit-ops is not published on PyPI, so "pipx install '
                'qbit-ops\\[tui]" alone will not work -- install from a '
                "repository checkout instead (see README.md, section "
                '"Install", for the clone URL):\n'
                "  cd <your qbit-ops checkout>\n"
                "  pipx uninstall qbit-ops  # only if already installed "
                "without the extra\n"
                r'  pipx install ".\[tui]"'
                "\n\n"
                "or, for local development:\n"
                "  poetry install --extras tui",
                ErrorCategory.CONFIGURATION,
            )

        from qbit_ops.tui.app import run_tui

        host: str | None = None
        try:
            host = error_boundary.load_qbit_config().host
        except ConfigError:
            host = None

        run_tui(
            client_factory=error_boundary.create_qbit_client,
            host=host,
            refresh_interval=interval,
        )

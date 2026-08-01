"""The canonical root Typer application: the CLI composition root."""

import typer

from qbit_ops import __version__
from qbit_ops.cli.commands import doctor as doctor_commands
from qbit_ops.cli.commands import tui as tui_commands
from qbit_ops.cli.commands.backup import backup_app
from qbit_ops.cli.commands.connection import connection_app
from qbit_ops.cli.commands.explain import explain_app
from qbit_ops.cli.commands.status import register as register_status
from qbit_ops.cli.commands.torrents import torrents_app
from qbit_ops.cli.commands.trackers import trackers_app
from qbit_ops.cli.exit_codes import ExitCode

PROJECT_NAME = "qbit-ops"

app = typer.Typer(add_completion=True, help="Administer qBittorrent.")
app.add_typer(connection_app, name="connection")
app.add_typer(backup_app, name="backup")
app.add_typer(torrents_app, name="torrents")
app.add_typer(trackers_app, name="trackers")
app.add_typer(explain_app, name="explain")

# Root-level singular commands are registered via a small `register(app)`
# function per module so command modules never import this composition
# root back (avoiding a circular import).
register_status(app)
doctor_commands.register(app)
tui_commands.register(app)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Print the project name and version when no command is provided."""
    if ctx.invoked_subcommand is None:
        typer.echo(f"{PROJECT_NAME} {__version__}")
        raise typer.Exit(code=ExitCode.SUCCESS)


if __name__ == "__main__":
    app()

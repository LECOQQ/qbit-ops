"""The `qbit-ops` command-line interface: Typer composition and presentation.

Split from the former monolithic `qbit_ops.main`/`qbit_ops.ui` (see
`docs/ARCHITECTURE.md`) into `app` (composition root), `commands/` (one
module per Typer command group), `rendering` (Rich/CLI presentation),
`validation` (CLI-facing input validation), `error_boundary` (exception-
to-exit-code mapping), and `exit_codes` (per-command exit-code enums).
Application/domain modules outside `qbit_ops/cli/` and `qbit_ops/tui/`
must never import from this package.
"""

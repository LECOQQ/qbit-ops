"""User-facing use cases shared by the CLI and TUI presentation layers.

Each module here represents one complete feature (backup, doctor,
explain, status, torrents, trackers, tracker_status) and may own
application-level orchestration, use-case-specific result models, and
calls through the qBittorrent boundary. Feature modules must stay free
of Typer, Rich, Textual, and any `qbit_ops.cli`/`qbit_ops.tui` import.
See docs/ARCHITECTURE.md for the strict `features/` vs `shared/` split.
"""

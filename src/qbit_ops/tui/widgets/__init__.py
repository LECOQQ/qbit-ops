"""Passive Textual widgets for `qbit_ops.tui`.

Every widget here only ever renders already-safe structured domain
data passed to it (or read from `TuiState`) -- no worker ownership, no
qBittorrent calls, no direct shared-state mutation. See
`qbit_ops.tui.app`'s module docstring for the full security boundary.
"""

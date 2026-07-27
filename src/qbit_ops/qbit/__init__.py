"""The qBittorrent boundary: client construction and raw-field interpretation.

This is the one place allowed to know the shape of a qbittorrent-api
response object and the one place that constructs a client. See
`qbit_ops/qbit/client.py` and `qbit_ops/qbit/fields.py`.

This is an extraction phase, not a compatibility-policy phase: no
version-support claim, capability matrix, or Docker-backed test lives
here yet -- see docs/COMPATIBILITY.md and
docs/audits/2026-07-package-refactor-plan.md (Phase 3 note).
"""

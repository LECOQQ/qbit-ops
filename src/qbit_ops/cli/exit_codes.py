"""Define every registered command's process exit-code scheme.

Moved verbatim from `qbit_ops.main` during the CLI reorganization (see
docs/ARCHITECTURE.md) -- no exit-code value or semantics changed. Kept
in its own module (not `cli/app.py`) so both `cli/app.py` (the
composition root) and every `cli/commands/*.py` module can import it
without a circular dependency: `app.py` registers command modules, so
command modules must not import `app.py` back.
"""

from enum import IntEnum


class ExitCode(IntEnum):
    """Define explicit process exit codes.

    These remain the exit-code semantics for every command other than
    `status`, which documents its own health-based exit codes below.

    `INTERNAL` (70) is shared by every command regardless of which exit
    code enum it otherwise uses: an unexpected programming defect
    (anything not explicitly classified as invalid input, not-found,
    ambiguous, unavailable, authentication, configuration, or
    cancellation) is never folded into a command's own scheme. See
    `qbit_ops.cli.error_boundary._catch_internal_errors` and
    docs/ERRORS_AND_EXIT_CODES.md.
    """

    SUCCESS = 0
    ERROR = 1
    NO_MATCH = 2
    INTERNAL = 70


class StatusExitCode(IntEnum):
    """Define exit codes specific to the `status` command.

    Deliberately separate from `ExitCode`: `status` reports operational
    health rather than success/failure, so its exit codes carry
    different semantics (0-3 map to `qbit_ops.status.Health`; 4 is reserved
    for invalid local configuration or invalid CLI usage). Other
    commands keep `ExitCode` until a later consolidation decision.

    `status --watch` does **not** use this health mapping: a running
    watch cannot continuously update the process exit code, and
    warning/critical/unavailable snapshots must never stop the loop. Its
    process exit code instead reports how the *process* ended: `0` for
    a clean user interrupt (`Ctrl+C`), `INVALID_USAGE` (4) for a
    pre-loop CLI/configuration failure, and `ExitCode.ERROR` (1) for an
    unexpected fatal error. See `docs/COMMANDS.md`.
    """

    HEALTHY = 0
    WARNING = 1
    CRITICAL = 2
    UNAVAILABLE = 3
    INVALID_USAGE = 4


class DoctorExitCode(IntEnum):
    """Define exit codes specific to the `doctor` command.

    `0`/`1`/`2` mirror `qbit_ops.doctor.doctor_exit_code`'s
    pass/warning/failure mapping exactly (kept here only for readability
    at call sites and in tests). `3` is intentionally unused: doctor has
    no condition that needs a fourth severity tier. `4` is reserved for
    invalid CLI invocation preventing doctor from starting — today
    `doctor` takes no options besides `--format`, and an invalid
    `--format` value is rejected by Click before the command body runs
    (Click's own usage-error exit code, not this one), so `4` is
    currently unreachable in practice. It is kept, not removed, so a
    future doctor-specific flag has a documented code to use instead of
    silently reusing `2` (already meaning "failure").
    """

    PASS = 0
    WARNING = 1
    FAILURE = 2
    INVALID_USAGE = 4


class TrackerStatusExitCode(IntEnum):
    """Define exit codes specific to the `trackers status` command.

    Mirrors `qbit_ops.tracker_status.tracker_status_exit_code`'s
    healthy/warning/critical/unavailable mapping exactly (kept here only
    for readability at call sites and in tests). Deliberately its own
    scheme rather than `ExitCode`: `2` here means CRITICAL, not
    `ExitCode.NO_MATCH` -- an empty selection (no torrents matched, or
    `--tracker` matched no observed identity) is not an error condition
    for this command and exits `0` via `HEALTHY`, not `2`, unlike
    `trackers inspect`'s no-match convention. `4` is reserved for
    invalid CLI invocation (e.g. contradictory filters), rejected before
    any qBittorrent API call.
    """

    HEALTHY = 0
    WARNING = 1
    CRITICAL = 2
    UNAVAILABLE = 3
    INVALID_USAGE = 4


class ExplainExitCode(IntEnum):
    """Define exit codes specific to the `explain` command group.

    `0`/`1`/`2` mirror `qbit_ops.explain.explanation_exit_code`'s
    info/warning-or-unknown/critical mapping exactly (kept here only for
    readability at call sites and in tests). `3` (`TARGET_UNAVAILABLE`)
    is used when there is nothing to explain at all -- an unresolved
    torrent hash, or a tracker identity with zero observations -- and is
    deliberately distinct from `ExplanationSeverity`-based `2`
    (CRITICAL): reusing a plain "not found" `2` here would be
    indistinguishable from a real critical finding, the same collision
    `TrackerStatusExitCode` already avoids for an empty selection. This
    also deliberately diverges from `ExitCode.NO_MATCH`/`trackers
    inspect`'s no-match convention (both `2`) for the same reason. An
    ambiguous hash prefix still reuses the shared `ExitCode.ERROR` (via
    `_fail_ambiguous_hash`), consistent with every other hash-driven
    command -- it is rejected before an explanation is ever computed, so
    it never enters this scheme at all. `4` is reserved for invalid CLI
    invocation; today `explain torrent`/`explain tracker` have no
    combination of options that can be locally contradictory, so it is
    currently unreachable in practice (see `DoctorExitCode` for the same
    pattern).
    """

    INFO = 0
    WARNING = 1
    CRITICAL = 2
    TARGET_UNAVAILABLE = 3
    INVALID_USAGE = 4


# Documents which exit-code enum governs each registered command's *normal*
# (non-internal) exit codes -- see docs/ERRORS_AND_EXIT_CODES.md for the
# full per-command tables. Every command additionally shares
# `ExitCode.INTERNAL` (70) for an unexpected programming defect via
# `qbit_ops.cli.error_boundary._catch_internal_errors`, regardless of which
# enum it is keyed to here. Read by `test_errors.py` to assert this table
# stays in sync with the commands actually registered on `app`.
EXIT_CODE_TABLE: dict[str, type[IntEnum]] = {
    "status": StatusExitCode,
    "doctor": DoctorExitCode,
    "tui": ExitCode,
    "connection check": ExitCode,
    "torrents list": ExitCode,
    "torrents categories": ExitCode,
    "torrents inspect": ExitCode,
    "torrents pause": ExitCode,
    "torrents resume": ExitCode,
    "torrents start": ExitCode,
    "torrents reannounce": ExitCode,
    "trackers add-if-present": ExitCode,
    "trackers list": ExitCode,
    "trackers status": TrackerStatusExitCode,
    "trackers inspect": ExitCode,
    "trackers replace": ExitCode,
    "trackers replace-passkey": ExitCode,
    "backup export": ExitCode,
    "backup diff": ExitCode,
    "trackers export": ExitCode,
    "trackers remove": ExitCode,
    "explain torrent": ExplainExitCode,
    "explain tracker": ExplainExitCode,
}

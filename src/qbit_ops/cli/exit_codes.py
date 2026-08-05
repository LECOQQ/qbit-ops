"""Define every registered command's process exit-code scheme."""

from enum import IntEnum


class ExitCode(IntEnum):
    """Define explicit process exit codes.

    `INTERNAL` (70) is shared by every command regardless of which exit
    code enum it otherwise uses: an unexpected programming defect is
    never folded into a command's own scheme.
    """

    SUCCESS = 0
    ERROR = 1
    NO_MATCH = 2
    INTERNAL = 70


class StatusExitCode(IntEnum):
    """Define exit codes specific to the `status` command.

    0-3 map to `qbit_core.features.status.Health`; 4 is invalid local
    configuration or CLI usage. `status --watch` does **not** use this
    health mapping: a running watch cannot continuously update the exit
    code, and warning/critical/unavailable snapshots must never stop the
    loop. Its exit code instead reports how the process ended: `0` for a
    clean `Ctrl+C`, `INVALID_USAGE` (4) for a pre-loop failure, and
    `ExitCode.ERROR` (1) for an unexpected fatal error.
    """

    HEALTHY = 0
    WARNING = 1
    CRITICAL = 2
    UNAVAILABLE = 3
    INVALID_USAGE = 4


class DoctorExitCode(IntEnum):
    """Define exit codes specific to the `doctor` command.

    `0`/`1`/`2` mirror `qbit_core.features.doctor.doctor_exit_code`'s
    pass/warning/failure mapping.
    """

    PASS = 0
    WARNING = 1
    FAILURE = 2
    INVALID_USAGE = 4


class TrackerStatusExitCode(IntEnum):
    """Define exit codes specific to the `trackers status` command.

    Deliberately its own scheme rather than `ExitCode`: `2` here means
    CRITICAL, not `ExitCode.NO_MATCH` -- an empty selection is not an
    error condition for this command and exits `0` via `HEALTHY`, unlike
    `trackers inspect`'s no-match convention.
    """

    HEALTHY = 0
    WARNING = 1
    CRITICAL = 2
    UNAVAILABLE = 3
    INVALID_USAGE = 4


class ExplainExitCode(IntEnum):
    """Define exit codes specific to the `explain` command group.

    `0`/`1`/`2` mirror `qbit_core.features.explain.explanation_exit_code`'s
    info/warning-or-unknown/critical mapping. `3` (`TARGET_UNAVAILABLE`)
    means there is nothing to explain at all (unresolved hash, or a
    tracker with zero observations), deliberately distinct from
    `CRITICAL` so it can never be mistaken for a real critical finding.
    """

    INFO = 0
    WARNING = 1
    CRITICAL = 2
    TARGET_UNAVAILABLE = 3
    INVALID_USAGE = 4


# Read by test_errors.py to assert this table matches the commands
# registered on `app`.
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
    "torrents import": ExitCode,
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

# Errors and Exit Codes

How `qbit-ops` classifies failures, validates input locally, and maps
outcomes to exit codes and rendered output. Complements
[docs/COMMANDS.md](COMMANDS.md#exit-codes), which documents the exact
per-command exit-code tables — this page documents the shared model
those tables are built from, so a script, a human, or a future TUI can
tell outcomes apart without parsing error strings.

## Table of Contents

- [Shared error categories](#shared-error-categories)
- [Fatal errors vs. operational reports](#fatal-errors-vs-operational-reports)
- [Local validation](#local-validation)
- [Exit-code policy](#exit-code-policy)
- [stdout/stderr contract](#stdoutstderr-contract)
- [Internal-error behavior](#internal-error-behavior)
- [Security guarantees](#security-guarantees)
- [Shell scripting guidance](#shell-scripting-guidance)
- [TUI-ready mapping](#tui-ready-mapping)

## Shared error categories

`qbit_ops.errors.ErrorCategory` classifies *why* a command failed,
independent of rendering or exit code:

| Category         | Meaning                                                             |
| ----------------- | -------------------------------------------------------------------- |
| `invalid_input`   | The invocation itself is invalid: blank `--hash`/`--tracker`, a contradictory filter combination, an unsupported `--format`. |
| `not_found`       | The invocation was valid and collection succeeded, but no target matched (unknown hash prefix, an unobserved tracker identity). |
| `ambiguous`       | A valid `--hash` prefix matches more than one torrent.               |
| `unavailable`     | Required remote data could not be collected because qBittorrent, or one of its endpoints, was unreachable. |
| `authentication`  | qBittorrent rejected the configured credentials.                     |
| `configuration`   | Local configuration (`.env`/environment) is missing or invalid.      |
| `cancelled`       | The user intentionally declined a confirmation prompt.               |
| `domain_failure`  | A handled failure that does not fit any category above (e.g. `--source`/`--target` conflict on a mutation). |
| `internal`        | An unexpected programming defect — not a remote or configuration failure. |

A category is a semantic label, **not** a numeric exit code: several
categories share a command's exit code today (e.g. every non-`internal`
handled failure on most read-only commands exits `1`), and the same
category maps to different codes across commands (`unavailable` is `3`
for `status`/`trackers status`/`explain`, but `1` everywhere else). See
[Exit-code policy](#exit-code-policy).

Every handled failure is recorded as an `qbit_ops.errors.AppError` (`category`,
`code`, `message`, optional `remediation`, optional `target`) before it is
rendered — `qbit_ops.cli.error_boundary.fail()`, `fail_status_usage()`, and
`fail_ambiguous_hash()` all build one. `qbit_ops.cli.error_boundary.last_app_error` always
holds the most recently constructed `AppError`, so tests (and, later, a
TUI) can assert on structured fields instead of parsing stderr text. See
[TUI-ready mapping](#tui-ready-mapping).

## Fatal errors vs. operational reports

A **fatal error** stops the command before it produces its normal
result: invalid input, an unreachable qBittorrent instance, a rejected
login, an internal defect. It is always reported through
`qbit_ops.cli.error_boundary.fail()` (or
an equivalent helper) and always exits non-zero.

An **operational finding** is a successfully collected report whose
content happens to be a warning or a critical value — a critical
tracker in `trackers status`, a failed check in `doctor`, a critical
finding in `explain`. This is not a fatal error: collection succeeded,
the report is complete and valid in every `--format`, and nothing is
written to stderr. Its exit code reflects the report's severity (see
each command's table in [docs/COMMANDS.md](COMMANDS.md#exit-codes)), not
a crash. Do not treat a critical operational report as an application
exception anywhere in the codebase — `doctor`, `trackers status`, and
`explain` are built specifically to keep this distinction.

## Local validation

Validated before configuration loading or any qBittorrent API call,
wherever practical, so a malformed identifier is never sent to
qBittorrent and then relabelled as a generic remote failure:

- `--hash`/`--tracker`/`--source`/`--target`/`--new-passkey`: rejected
  when empty or whitespace-only, via the shared
  `qbit_ops.errors.require_non_blank()` — see `torrents inspect`, every
  bulk torrent mutation, `explain torrent`/`explain tracker`, and
  every `trackers` mutation command.
- Contradictory filter combinations (`--completed --incomplete`,
  `--active --inactive`) and unknown `--state` values: rejected by
  `qbit_ops.torrents.build_torrent_filter()`.
- A blank/whitespace `--tracker` used as a *filter* (`torrents list`,
  `trackers status`, every bulk mutation's `--tracker`) is rejected by
  the same `build_torrent_filter()` call, before any client is
  created — distinct from a silently-empty filter value, which would
  otherwise normalize to `""` and match nothing without explaining why.
- Empty repeated `--category`/`--state` values are dropped, not
  rejected: `--category ""` is treated as "no category filter
  contributed by this occurrence" rather than an error, consistent
  with `build_torrent_filter()`'s existing, tested behavior.
- Unsupported `--format` combinations: rejected by
  `qbit_ops.cli.validation.validate_format_support()` against the
  `FORMAT_SUPPORT`
  table (see [docs/COMMANDS.md](COMMANDS.md#format-support-matrix)).
- `status --watch`'s own option combinations (`--quiet` with `--watch`,
  an unsupported `--format`, a non-finite `--interval`): rejected by
  `status`'s own pre-loop validation.

`torrents inspect --hash "   "`, `explain torrent --hash "   "`, and
`explain tracker --tracker "   "` all fail with `invalid_input` before
any network call — verified in `tests/test_errors.py`.

## Exit-code policy

Two layers, matching `qbit_ops.cli.exit_codes.EXIT_CODE_TABLE`:

### Shared process-level principles

Reserved where practical, but **not** a global guarantee — see each
command's own table in [docs/COMMANDS.md](COMMANDS.md#exit-codes) for
the authoritative mapping:

```text
0   successful command or intentional cancellation
1   command-specific warning or generic handled failure
2   command-specific critical/no-match result, or a parser usage error
    Typer/Click owns before the command body ever runs
3   unavailable remote data (status / trackers status / explain / doctor's
    equivalent "3" is intentionally unused, see docs/COMMANDS.md)
4   validated local invocation/configuration error emitted by qbit-ops
    itself (status / doctor / trackers status / explain)
70  unexpected internal software failure (ErrorCategory.INTERNAL),
    shared by every command regardless of its own scheme
```

Numeric exit codes must always be interpreted in the context of the
invoked command — see `qbit_ops.cli.exit_codes.EXIT_CODE_TABLE` and
[docs/COMMANDS.md](COMMANDS.md#exit-codes) for the exact per-command
tables. This phase intentionally did not redesign any existing exit
code purely for cross-command uniformity; it only added the shared `70`
and the local-validation fast paths above.

### Command-specific tables

See [docs/COMMANDS.md](COMMANDS.md#exit-codes) for `status`, `doctor`,
`trackers status`, `explain`, torrent selection/mutations, and `backup
diff`. `connection check` uses plain `ExitCode` (`0`/`1`); it has no
usage or no-match condition of its own.

### `ExplainExitCode.TARGET_UNAVAILABLE` naming note

`ExplainExitCode.TARGET_UNAVAILABLE` (`3`) is semantically this page's
`not_found` category (an unresolved hash, or a tracker identity with
zero observations), not `unavailable` — its name predates this phase's
category vocabulary. It is kept as-is (see docs/DECISIONS.md,
2026-07-25) rather than renamed, consistent with "do not redesign every
exit code merely for aesthetic uniformity." A genuine `unavailable`
(qBittorrent unreachable) on `explain torrent`/`explain tracker` instead
exits `1` (`ExitCode.ERROR`), via the shared `qbit_error_boundary()` —
distinct from `3` by design.

## stdout/stderr contract

One project-wide rule for fatal command errors: **a fatal error is
reported on stderr only; stdout stays empty.** No command emits a
partial or malformed success payload — every collection happens inside
the same guarded block as client creation
(`qbit_ops.cli.error_boundary.qbit_error_boundary()`),
strictly before any rendering begins, so a failure never has partial
output to leave behind. This applies identically to every `--format`;
a machine-readable consumer parsing stdout for JSON/JSONL/CSV can
assume any content there is a complete, valid payload, and treat empty
stdout with a non-zero exit code as the fatal-error case.

Structured *operational reports* (see above) are the opposite case:
their full report — including a `warning`/`critical`/`fail` status — is
always written to stdout in every `--format`, never stderr, and stderr
stays empty on that path (see `doctor`, `trackers status`, `explain`,
and `status`'s existing per-command silence-contract tests).

## Internal-error behavior

Every registered command is wrapped by
`qbit_ops.cli.error_boundary.catch_internal_errors`,
the outermost boundary: it re-raises `typer.Exit` and `KeyboardInterrupt`
untouched, and converts anything else into a concise `Internal error:
<ExceptionType>: <message>` line on stderr plus `ExitCode.INTERNAL`
(`70`) — never a traceback, never `repr()` of the raw exception, and
never converted into `unavailable`/`3` or a command's own warning/
critical code.

`qbit_ops.cli.error_boundary.qbit_error_boundary()` — used around client
creation plus one domain call in most commands — only degrades
`ConfigError`, `QbitAuthenticationError`, `QbitConnectionError`, and
`(qbittorrentapi.APIError, OSError)` into a rendered, categorized
`fail()`. A bare `RuntimeError`, `TypeError`, `AssertionError`, or any
other exception not in that set is deliberately left to propagate to
`catch_internal_errors` instead of being silently relabelled as a
remote failure. `status`'s one-shot collection
(`_collect_status_snapshot_safely()`) and its `--watch` collection
(`_collect_status_snapshot_for_watch()`) apply the same discipline for
the same reason — see their docstrings.

`doctor` is the deliberate exception: its own connection check treats
*any* exception during login as `ConnectionOutcome.CONNECTION_FAILED`,
because probing whether qBittorrent is reachable is exactly what that
check exists to report, and the outcome becomes a structured `fail`
check in the report rather than a fatal error. This is a documented,
intentional scope boundary, not an oversight — see
[Fatal errors vs. operational reports](#fatal-errors-vs-operational-reports).

There is no debug/verbose flag that exposes a raw traceback today; if
one is added later, it must not print credentials or a raw tracker URL
under any circumstance (see below).

## Security guarantees

- `print_error()` runs `sanitize_tracker_text()` unconditionally, so
  every rendered error — handled or internal — has any embedded tracker
  URL/credential/userinfo redacted before it reaches stderr.
- `AppError.message`/`.target` never carry a raw tracker URL or
  credential; `target` is always a safe identity (a hash prefix, a
  normalized `host[:port]`).
- An internal-error message includes the exception's type name and
  `str(exception)` only — never `repr()`, never a traceback, never a
  raw request body. If a future exception type embeds a secret in
  `str()`, it still passes through `sanitize_tracker_text()` via
  `print_error()`.
- See `tests/test_tracker_security.py` and
  `tests/test_errors.py::test_internal_error_does_not_leak_credentials`.

## Shell scripting guidance

Health-based commands (`status`, `doctor`, `trackers status`, `explain`)
are safe to branch on directly:

```bash
qbit-ops status --quiet
case $? in
  0) echo healthy ;;
  1) echo warning ;;
  2) echo critical ;;
  3) echo unavailable ;;
  4) echo "invalid usage/configuration" ;;
  70) echo "internal qbit-ops error — file a bug" ;;
esac
```

This table applies specifically to `status`; every other command has
its own table in [docs/COMMANDS.md](COMMANDS.md#exit-codes). `70` can
appear after *any* command, regardless of its own scheme — a script
that wants to distinguish "expected outcome" from "qbit-ops itself
broke" should check for `70` first.

## TUI-ready mapping

`qbit_ops.errors.AppError` is the application-layer representation a future
TUI can consume without parsing rendered text:

```python
@dataclass(frozen=True)
class AppError:
    category: ErrorCategory
    code: str
    message: str
    remediation: str | None = None
    target: str | None = None
```

Planned mapping (not implemented in this phase — no TUI widgets exist
yet):

| Category         | TUI state                                   |
| ----------------- | --------------------------------------------- |
| `invalid_input`   | Inline validation message.                    |
| `not_found`       | Empty/not-found state.                        |
| `ambiguous`       | Candidate-selection prompt.                   |
| `unavailable`     | Recoverable connection banner.                |
| `authentication`  | Blocking authentication state.                |
| `configuration`   | Blocking configuration state.                 |
| `cancelled`       | No state change; return to the previous view. |
| `domain_failure`  | Inline error message with `remediation`.      |
| `internal`        | Fatal application notification.               |

"""Own CLI-facing input validation and parameter normalization.

Rejects an unsupported `--format` or a blank `--hash`/`--tracker`/etc.
before any qBittorrent API call. Deliberately narrow -- this is not a
second domain validation layer;
domain rules (torrent filter combinations, tracker template shape,
mutation planning) stay in their own modules (`qbit_core.features.torrents`,
`qbit_core.features.trackers`, ...).
"""

from qbit_core.errors import ErrorCategory, InvalidInputError, require_non_blank
from qbit_ops.cli.error_boundary import fail
from qbit_ops.cli.rendering import OutputFormat

# Every read-only command uses the same `qbit_ops.cli.rendering.OutputFormat`
# enum and the same `--format` option, but not every command can
# meaningfully render every format (see docs/COMMANDS.md "Format Support
# Matrix"). This table is the single source of truth: both
# `validate_format_support` and the CLI test suite read it, so the
# implementation, its validation, and its tests cannot
# drift apart.
FORMAT_SUPPORT: dict[str, frozenset[OutputFormat]] = {
    "status": frozenset(OutputFormat),
    "connection_check": frozenset(OutputFormat),
    "doctor": frozenset(OutputFormat),
    "version": frozenset(
        {OutputFormat.table, OutputFormat.json, OutputFormat.jsonl}
    ),
    "torrents_list": frozenset(OutputFormat),
    "torrents_stats": frozenset(OutputFormat),
    "torrents_categories": frozenset(OutputFormat),
    "torrents_inspect": frozenset(
        {OutputFormat.table, OutputFormat.json, OutputFormat.jsonl}
    ),
    "torrents_import": frozenset({OutputFormat.table, OutputFormat.json}),
    # Bulk actions: table or json, never jsonl/csv -- a mutation
    # produces one result, not a stream of rows.
    "torrents_pause": frozenset({OutputFormat.table, OutputFormat.json}),
    "torrents_resume": frozenset({OutputFormat.table, OutputFormat.json}),
    "torrents_start": frozenset({OutputFormat.table, OutputFormat.json}),
    "torrents_reannounce": frozenset({OutputFormat.table, OutputFormat.json}),
    "torrents_delete": frozenset({OutputFormat.table, OutputFormat.json}),
    "torrents_category_set": frozenset({OutputFormat.table, OutputFormat.json}),
    "torrents_category_clear": frozenset(
        {OutputFormat.table, OutputFormat.json}
    ),
    "torrents_tag_add": frozenset({OutputFormat.table, OutputFormat.json}),
    "torrents_tag_remove": frozenset({OutputFormat.table, OutputFormat.json}),
    "torrents_search": frozenset(OutputFormat),
    "backup_restore": frozenset({OutputFormat.table, OutputFormat.json}),
    "trackers_list": frozenset(OutputFormat),
    "trackers_status": frozenset(OutputFormat),
    "trackers_inspect": frozenset(OutputFormat),
    "trackers_export": frozenset(
        {OutputFormat.table, OutputFormat.json, OutputFormat.jsonl}
    ),
    "backup_export": frozenset(
        {OutputFormat.table, OutputFormat.json, OutputFormat.jsonl}
    ),
    "backup_diff": frozenset(
        {OutputFormat.table, OutputFormat.json, OutputFormat.jsonl}
    ),
    "explain_torrent": frozenset(
        {OutputFormat.table, OutputFormat.json, OutputFormat.jsonl}
    ),
    "explain_tracker": frozenset(
        {OutputFormat.table, OutputFormat.json, OutputFormat.jsonl}
    ),
}


def validate_format_support(
    command_id: str,
    output_format: OutputFormat,
) -> None:
    """Reject a format a command cannot meaningfully render.

    Called before any qBittorrent API call, so an unsupported format
    fails fast without a network round-trip. `table` and `json` are
    supported by every command; `jsonl` and `csv` are only offered where
    they have a stable, non-artificial representation (see
    `FORMAT_SUPPORT` above).
    """
    supported = FORMAT_SUPPORT[command_id]
    if output_format in supported:
        return

    supported_names = ", ".join(sorted(fmt.value for fmt in supported))
    fail(
        f"--format {output_format.value} is not supported here "
        f"(no stable representation). Supported formats: {supported_names}."
    )


def validate_hash_option(
    value: str | None, *, option_name: str = "--hash"
) -> str | None:
    """Reject a blank/whitespace-only `--hash` before any API call.

    Returns `None` unchanged so callers can keep treating "not
    provided" and "validated, non-blank value" as the only two cases.
    """
    if value is None:
        return None
    try:
        return require_non_blank(value, field_name=option_name)
    except InvalidInputError as error:
        fail(str(error), ErrorCategory.INVALID_INPUT)


def validate_category_name(value: str) -> str:
    """Reject a blank/whitespace-only category name before any API call."""
    try:
        return require_non_blank(value, field_name="category")
    except InvalidInputError as error:
        fail(str(error), ErrorCategory.INVALID_INPUT)


def validate_tag_names(values: list[str]) -> tuple[str, ...]:
    """Strip, drop blanks and deduplicate one or more `tag add`/`tag
    remove` names, rejecting an empty result before any API call.
    """
    normalized = tuple(
        dict.fromkeys(value.strip() for value in values if value.strip() != "")
    )
    if not normalized:
        fail(
            "Provide at least one non-blank tag name.",
            ErrorCategory.INVALID_INPUT,
        )
    return normalized

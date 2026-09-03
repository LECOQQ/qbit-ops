"""Test shell completion for options whose values are a closed set.

`qbit_ops.cli.completion.complete_choices` is unit-tested directly,
then exercised end to end through the real Typer app the way a shell
actually invokes it -- `_QBIT_OPS_COMPLETE=complete_<shell>` plus
`COMP_WORDS`/`COMP_CWORD`, matching what `qbit-ops --install-completion`
wires into a shell profile (see README.md, "Shell completion").
"""

import contextlib
import io
import os
from collections.abc import Iterator

import pytest
from typer.main import get_command

from qbit_ops.cli import error_boundary
from qbit_ops.cli.app import app
from qbit_ops.cli.completion import complete_choices

_CLICK_COMMAND = get_command(app)


def _complete(comp_words: str, cword: int) -> list[str]:
    """Run one shell-completion request in-process, as the real hook does.

    `cmd.main(..., standalone_mode=False)` takes the same
    `_main_shell_completion` path click's `main()` does on every
    keypress; running it in-process (rather than shelling out to the
    installed console script) keeps these tests fast and independent
    of `$PATH`.
    """
    os.environ["_QBIT_OPS_COMPLETE"] = "complete_bash"
    os.environ["COMP_WORDS"] = comp_words
    os.environ["COMP_CWORD"] = str(cword)
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            try:
                _CLICK_COMMAND.main(
                    args=[],
                    prog_name="qbit-ops",
                    complete_var="_QBIT_OPS_COMPLETE",
                    standalone_mode=False,
                )
            except SystemExit:
                pass
    finally:
        del os.environ["_QBIT_OPS_COMPLETE"]
        del os.environ["COMP_WORDS"]
        del os.environ["COMP_CWORD"]
    return [line for line in buffer.getvalue().splitlines() if line]


@pytest.fixture(autouse=True)
def _forbid_qbit_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Fail loudly if completing a closed enum ever reaches qBittorrent.

    This is the invariant the work-item exists to protect: Tab must
    never depend on a live instance. Every test in this module runs
    under it, so a completer that regresses into a network call fails
    here before it fails on someone's seedbox.
    """

    def _forbidden(**kwargs: object) -> None:
        raise AssertionError(
            "shell completion must never build a qBittorrent client"
        )

    monkeypatch.setattr(error_boundary, "create_qbit_client", _forbidden)
    yield


# --- complete_choices() in isolation ----------------------------------------


def test_complete_choices_returns_every_value_sorted_when_empty() -> None:
    complete = complete_choices({"seeding", "errored", "checking"})
    assert complete("") == ["checking", "errored", "seeding"]


def test_complete_choices_filters_by_case_sensitive_prefix() -> None:
    complete = complete_choices({"healthy", "critical", "disabled"})
    assert complete("h") == ["healthy"]
    assert complete("H") == []


def test_complete_choices_returns_nothing_for_an_unmatched_prefix() -> None:
    complete = complete_choices({"table", "json"})
    assert complete("zz") == []


def test_complete_choices_deduplicates_repeated_values() -> None:
    complete = complete_choices(["seeding", "seeding"])
    assert complete("") == ["seeding"]


# --- end-to-end, through the real Typer app ---------------------------------


@pytest.mark.parametrize(
    ("comp_words", "expected"),
    [
        (
            "qbit-ops torrents list --state ",
            [
                "checking",
                "downloading",
                "errored",
                "seeding",
                "stalled",
                "unknown",
            ],
        ),
        ("qbit-ops torrents list --state do", ["downloading"]),
        (
            "qbit-ops torrents list --exclude-state ",
            [
                "checking",
                "downloading",
                "errored",
                "seeding",
                "stalled",
                "unknown",
            ],
        ),
        (
            "qbit-ops torrents list --tracker-health ",
            ["critical", "disabled", "healthy", "unknown", "warning"],
        ),
        ("qbit-ops torrents list --tracker-health h", ["healthy"]),
        (
            "qbit-ops trackers status --state ",
            [
                "checking",
                "downloading",
                "errored",
                "seeding",
                "stalled",
                "unknown",
            ],
        ),
        (
            "qbit-ops torrents list --sort ",
            [
                "added_on",
                "category",
                "down",
                "name",
                "progress",
                "ratio",
                "seeding_time",
                "size",
                "state",
                "up",
            ],
        ),
        (
            "qbit-ops torrents list --sort s",
            ["seeding_time", "size", "state"],
        ),
        (
            "qbit-ops trackers list --sort ",
            ["ratio", "size", "torrents", "tracker", "uploaded"],
        ),
    ],
)
def test_closed_enum_options_complete_their_values(
    comp_words: str, expected: list[str]
) -> None:
    assert _complete(comp_words, 4) == expected


def test_format_completes_via_typer_s_native_enum_support() -> None:
    """`--format` needs no wiring here: `OutputFormat` is a `StrEnum`,

    and Typer/Click already turn any `Enum`-typed option into a
    `click.Choice`, which completes on its own (`Choice.shell_complete`).
    This test exists to prove that stays true, not to add behavior.
    """
    assert _complete("qbit-ops torrents list --format ", 4) == [
        "table",
        "json",
        "jsonl",
        "csv",
    ]


def test_category_is_deliberately_not_completed() -> None:
    """`--category` values live on the instance; completing them would

    mean a qBittorrent call on every Tab press. No completer is wired
    for it, so the shell falls back to ordinary path completion --
    which yields nothing for a bare `--category `.
    """
    assert _complete("qbit-ops torrents list --category ", 4) == []

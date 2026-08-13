"""Lock the observable output contracts of torrent selection.

Safety net for the SELECT/INSPECT consolidation:
these assertions must keep passing, unchanged, while `SelectedTorrent`
is replaced by `TorrentSnapshot` and the tracker enrichment moves into
a named INSPECT stage.

Deliberately black-box: every assertion reads CLI stdout/stderr, an
exit code, or a recorded qBittorrent call. Nothing here names
`SelectedTorrent`, `TorrentSelection`, or any domain object's
attribute, so a refactor of those types can never require editing this
file -- which is the whole point of it.

Not duplicated here: the `Trackers` column's presence/absence
(`tests/test_torrent_filters_cli.py`) and the hash selector's
exact/ambiguous/absent behaviour (`tests/test_torrents_cli.py`), both
already locked at the CLI level. The TUI half of the same contract
lives in `tests/test_tui_app.py`.
"""

import csv
import io
import json

from typer.testing import CliRunner

from qbit_ops.cli.app import app
from qbit_ops.cli.exit_codes import ExitCode
from tests.support import FakeQbitClient, make_torrent

TORRENT_A = "a" * 40
TORRENT_B = "b" * 40
TORRENT_C = "c" * 40
TRACKER_HOST = "tracker.example"
TRACKER_URL = f"https://{TRACKER_HOST}/announce"


def _csv_rows(stdout: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(stdout)))


def _tracker_count_column(rows: list[list[str]]) -> int:
    return rows[0].index("tracker_count")


def _category_column(rows: list[list[str]]) -> int:
    return rows[0].index("category")


def _client_with_one_tracked_torrent() -> FakeQbitClient:
    return FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, name="A", category="movies")],
        trackers_by_hash={TORRENT_A: [{"url": TRACKER_URL, "status": "2"}]},
    )


# --- tracker_count: "not collected" is not "zero" ---------------------------


def test_list_json_distinguishes_uncollected_tracker_count_from_zero(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """`null` means no tracker inspection happened; an integer means it did.

    Merging the two would silently report "no trackers" for every
    torrent of an uninspected listing.
    """
    configure_qbit_backend(client=_client_with_one_tracked_torrent())

    uninspected = runner.invoke(app, ["torrents", "list", "--format", "json"])
    inspected = runner.invoke(
        app,
        ["torrents", "list", "--tracker", TRACKER_HOST, "--format", "json"],
    )

    assert uninspected.exit_code == ExitCode.SUCCESS
    assert inspected.exit_code == ExitCode.SUCCESS
    assert json.loads(uninspected.stdout)["torrents"][0]["tracker_count"] is (
        None
    )
    assert json.loads(inspected.stdout)["torrents"][0]["tracker_count"] == 1


def test_list_csv_leaves_tracker_count_blank_when_not_collected(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_client_with_one_tracked_torrent())

    uninspected = _csv_rows(
        runner.invoke(app, ["torrents", "list", "--format", "csv"]).stdout
    )
    inspected = _csv_rows(
        runner.invoke(
            app,
            ["torrents", "list", "--tracker", TRACKER_HOST, "--format", "csv"],
        ).stdout
    )

    column = _tracker_count_column(uninspected)
    assert uninspected[1][column] == ""
    assert inspected[1][_tracker_count_column(inspected)] == "1"


# --- the `(uncategorized)` display label ------------------------------------


def test_list_renders_the_uncategorized_label_in_every_format(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """qBittorrent reports an empty category; `torrents list` reports
    `(uncategorized)` -- the same token `--category` accepts.
    """
    configure_qbit_backend(
        client=FakeQbitClient(
            torrents=[make_torrent(hash=TORRENT_A, name="A", category="")]
        )
    )

    table = runner.invoke(app, ["torrents", "list"])
    payload = runner.invoke(app, ["torrents", "list", "--format", "json"])
    rows = _csv_rows(
        runner.invoke(app, ["torrents", "list", "--format", "csv"]).stdout
    )

    assert table.exit_code == ExitCode.SUCCESS
    assert "(uncategorized)" in table.stdout
    assert (
        json.loads(payload.stdout)["torrents"][0]["category"]
        == "(uncategorized)"
    )
    assert rows[1][_category_column(rows)] == "(uncategorized)"


def test_list_keeps_a_real_category_untouched(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(
        client=FakeQbitClient(
            torrents=[make_torrent(hash=TORRENT_A, name="A", category="movies")]
        )
    )

    result = runner.invoke(app, ["torrents", "list", "--format", "json"])

    assert result.exit_code == ExitCode.SUCCESS
    assert json.loads(result.stdout)["torrents"][0]["category"] == "movies"


# --- bounded API calls: cheap filters before expensive inspection -----------


def test_list_without_a_tracker_filter_never_inspects_trackers(
    runner: CliRunner, configure_qbit_backend
) -> None:
    client = _client_with_one_tracked_torrent()
    configure_qbit_backend(client=client)

    result = runner.invoke(app, ["torrents", "list"])

    assert result.exit_code == ExitCode.SUCCESS
    assert client.torrents_info_calls == 1
    assert client.torrents_trackers_calls == 0


def test_list_inspects_only_the_torrents_surviving_the_cheap_filters(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Cheap `torrents_info()`-shaped filters run first, so a combined
    `--category --tracker` costs one tracker lookup, not one per torrent.
    """
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=TORRENT_A, name="A", category="movies"),
            make_torrent(hash=TORRENT_B, name="B", category="series"),
            make_torrent(hash=TORRENT_C, name="C", category="music"),
        ],
        trackers_by_hash={
            TORRENT_A: [{"url": TRACKER_URL, "status": "2"}],
            TORRENT_B: [{"url": TRACKER_URL, "status": "2"}],
            TORRENT_C: [{"url": TRACKER_URL, "status": "2"}],
        },
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "torrents",
            "list",
            "--category",
            "movies",
            "--tracker",
            TRACKER_HOST,
            "--format",
            "json",
        ],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert [t["name"] for t in json.loads(result.stdout)["torrents"]] == ["A"]
    assert client.torrents_info_calls == 1
    assert client.torrents_trackers_calls == 1


def test_bulk_mutation_inspects_only_torrents_surviving_cheap_filters(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """The same bound applies to a mutation's planning scan -- a preview
    must not cost one tracker lookup per torrent in the instance.
    """
    client = FakeQbitClient(
        torrents=[
            make_torrent(
                hash=TORRENT_A, name="A", category="movies", state="uploading"
            ),
            make_torrent(
                hash=TORRENT_B, name="B", category="series", state="uploading"
            ),
        ],
        trackers_by_hash={
            TORRENT_A: [{"url": TRACKER_URL, "status": "2"}],
            TORRENT_B: [{"url": TRACKER_URL, "status": "2"}],
        },
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "torrents",
            "pause",
            "--category",
            "movies",
            "--tracker",
            TRACKER_HOST,
        ],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert client.paused_hashes == []
    assert client.torrents_trackers_calls == 1
